"""Table driven families (SD 1.x, SDXL): the port of the Swift importer's key matching loops.

SD 1.x and SDXL map to Draw Things layers through big literal tables (``data/lora_mappings.json``,
extracted from ``ModelMapping.swift`` by ``tools/drawthings/extract_swift_mappings.py``). The lookup
below is a port of the Swift importer's loops, including its quirks, because the key names found in
the wild (kohya ``lora_unet_...``, diffusers, PEFT) all go through the same fuzzy matching.
Moved unchanged from the old single-file converter.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

import numpy as np

from dtflow.services.lora_convert.context import ConversionContext
from dtflow.services.lora_convert.errors import (
    COLAB_HINT,
    ShapeMismatchError,
    UnmatchedModulesError,
    UnsupportedLoraFormatError,
)
from dtflow.services.lora_convert.keys import split_key
from dtflow.services.lora_convert.numeric import is_diagonal_up, scalar, scale_down, scale_up
from dtflow.services.lora_convert.safetensors_io import SafeTensors


@functools.cache
def _mappings() -> dict:
    return json.loads(Path(__file__).parents[1].joinpath("data", "lora_mappings.json").read_text(encoding="utf-8"))


def _sdxl_partials() -> list[tuple[str, str]]:
    """Original UNet names to diffusers names for the SDXL layout (3 levels, no attention on the first)."""
    pairs: list[tuple[str, str]] = []
    for block, (level, index) in {1: (0, 0), 2: (0, 1), 4: (1, 0), 5: (1, 1), 7: (2, 0), 8: (2, 1)}.items():
        pairs.append((f"input_blocks.{block}.0.", f"down_blocks.{level}.resnets.{index}."))
        if level > 0:
            pairs.append((f"input_blocks.{block}.1.", f"down_blocks.{level}.attentions.{index}."))
    for block, level in {3: 0, 6: 1}.items():
        pairs.append((f"input_blocks.{block}.0.op.", f"down_blocks.{level}.downsamplers.0.conv."))
    pairs += [
        ("middle_block.0.", "mid_block.resnets.0."),
        ("middle_block.1.", "mid_block.attentions.0."),
        ("middle_block.2.", "mid_block.resnets.1."),
    ]
    for block in range(9):
        level, index = divmod(block, 3)
        pairs.append((f"output_blocks.{block}.0.", f"up_blocks.{level}.resnets.{index}."))
        if level < 2:
            pairs.append((f"output_blocks.{block}.1.", f"up_blocks.{level}.attentions.{index}."))
    pairs += [
        ("output_blocks.2.2.", "up_blocks.0.upsamplers.0."),
        ("output_blocks.5.2.", "up_blocks.1.upsamplers.0."),
        ("output_blocks.8.1.", "up_blocks.2.upsamplers.0."),
    ]
    # Anchored and last: a bare "out.0." would also match every "to_out.0." attention key.
    pairs += [
        ("diffusion_model.time_embed.0.", "diffusion_model.time_embedding.linear_1."),
        ("diffusion_model.time_embed.2.", "diffusion_model.time_embedding.linear_2."),
        ("diffusion_model.label_emb.0.0.", "diffusion_model.add_embedding.linear_1."),
        ("diffusion_model.label_emb.0.2.", "diffusion_model.add_embedding.linear_2."),
        ("diffusion_model.input_blocks.0.0.", "diffusion_model.conv_in."),
        ("diffusion_model.out.0.", "diffusion_model.conv_norm_out."),
        ("diffusion_model.out.2.", "diffusion_model.conv_out."),
    ]
    return pairs


def _with_diffusers_names(
    table: dict[str, list[str]], partials: list[tuple[str, str]], resnets: list[tuple[str, str]]
) -> dict[str, tuple[str, ...]]:
    out = {key: tuple(names) for key, names in table.items()}
    for key, names in table.items():
        diffusers = key
        for old, new in partials:
            if old in key:
                diffusers = key.replace(old, new)
                break
        if ".resnets." in diffusers:
            for old, new in resnets:
                if old in diffusers:
                    diffusers = diffusers.replace(old, new)
                    break
        if diffusers != key:
            out[diffusers] = tuple(names)
    return out


def _expand_unet(mapping: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    out = dict(mapping)
    for key, value in mapping.items():
        parts = key.split(".")
        if parts[0] == "model":
            if len(parts) > 3:
                out["_".join(parts[2:-1]) + "." + parts[-1]] = value
            if len(parts) > 2:
                out["_".join(parts[1:-1]) + "." + parts[-1]] = value
        elif parts[0] == "diffusion_model":
            if len(parts) > 2:
                out["_".join(parts[1:-1]) + "." + parts[-1]] = value
        elif len(parts) > 1:
            out["_".join(parts[:-1]) + "." + parts[-1]] = value
    return out


def _clip_text(layers: int) -> dict[str, tuple[str, ...]]:
    """HF style CLIP text encoder table (``CLIPTextModel`` and ``OpenCLIPTextModelGTransformers``)."""
    prefix = "cond_stage_model.transformer.text_model"
    out: dict[str, tuple[str, ...]] = {
        f"{prefix}.embeddings.position_embedding.weight": ("t-0-0",),
        f"{prefix}.embeddings.token_embedding.weight": ("t-1-0",),
    }
    leaves = (
        ("layer_norm1", 2),
        ("self_attn.q_proj", 3),
        ("self_attn.k_proj", 4),
        ("self_attn.v_proj", 5),
        ("self_attn.out_proj", 6),
        ("layer_norm2", 7),
        ("mlp.fc1", 8),
        ("mlp.fc2", 9),
    )
    for i in range(layers):
        for leaf, offset in leaves:
            out[f"{prefix}.encoder.layers.{i}.{leaf}.weight"] = (f"t-{i * 8 + offset}-0",)
            out[f"{prefix}.encoder.layers.{i}.{leaf}.bias"] = (f"t-{i * 8 + offset}-1",)
    final = layers * 8 + 2
    out[f"{prefix}.final_layer_norm.weight"] = (f"t-{final}-0",)
    out[f"{prefix}.final_layer_norm.bias"] = (f"t-{final}-1",)
    return out


def _expand_text(mapping: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    out = dict(mapping)
    for key, value in mapping.items():
        parts = key.split(".")
        for i in range(3):
            if len(parts) - 1 <= i:
                break
            out["_".join(parts[i:-1]) + "." + parts[-1]] = value
    return out


def _normalize_state(keys: list[str], te1_tag: str = "te") -> dict[str, str]:
    """The Swift importer's key fix-ups: returns normalized key -> real key in the file."""
    state = {k: k for k in keys}

    def copy_alpha(key: str, suffix: str, alpha: str, new_key: str) -> None:
        if key.endswith(suffix) and key[: -len(suffix)] + alpha in state:
            state[new_key + ".alpha"] = state[key[: -len(suffix)] + alpha]

    for key in keys:  # lora_A / lora_B (PEFT, LyCORIS)
        if not (key.endswith(".lora_A.weight") or key.endswith(".lora_B.weight")):
            continue
        comps = key.split(".")
        if len(comps) < 3:
            continue
        if comps[1] == "base_model":
            del comps[1]
        if comps[0] == "base_model":
            del comps[0]
        new_key = "_".join(comps[:-2])
        if key.endswith(".lora_B.weight"):
            copy_alpha(key, ".lora_B.weight", ".alpha", new_key)
            state[new_key + ".lora_up.weight"] = key
        else:
            state[new_key + ".lora_down.weight"] = key
    for key in keys:  # diffusers ".lora.up.weight"
        if not (key.endswith(".lora.up.weight") or key.endswith(".lora.down.weight")):
            continue
        comps = key.split(".")
        if len(comps) < 4:
            continue
        if comps[1] == "base_model":
            del comps[1]
        if comps[0] == "base_model":
            del comps[0]
        new_key = "_".join(comps[:-3])
        if key.endswith(".lora.up.weight"):
            copy_alpha(key, ".lora.up.weight", ".alpha", new_key)
            state[new_key + ".lora_up.weight"] = key
        else:
            state[new_key + ".lora_down.weight"] = key
    for key in keys:  # OneTrainer
        if not (key.endswith(".lora_up.weight") or key.endswith(".lora_down.weight")):
            continue
        comps = key.split(".")
        if len(comps) < 4:
            continue
        if comps[0] == "transformer":
            del comps[0]
        new_key = "_".join(comps[:-2])
        if key.endswith(".lora_up.weight"):
            copy_alpha(key, ".lora_up.weight", ".alpha", new_key)
            state[new_key + ".lora_up.weight"] = key
        else:
            state[new_key + ".lora_down.weight"] = key
    for key in keys:  # ".lora_A.default.weight"
        if not (key.endswith(".lora_A.default.weight") or key.endswith(".lora_B.default.weight")):
            continue
        comps = key.split(".")
        if len(comps) < 3:
            continue
        if comps[1] == "base_model":
            del comps[1]
        if comps[0] == "base_model":
            del comps[0]
        new_key = "_".join(comps[:-3])
        if key.endswith(".lora_B.default.weight"):
            copy_alpha(key, ".lora_B.default.weight", ".alpha", new_key)
            state[new_key + ".lora_up.weight"] = key
        else:
            state[new_key + ".lora_down.weight"] = key
    for key in keys:
        if key.startswith("lora_unet__"):
            state[key[11:]] = key
    # Diffusers/PEFT text encoder names -> the kohya spelling the tables are keyed on:
    # text_encoder_2_* is the second encoder (te2), text_encoder_* the first (te / te1).
    for key in list(state):
        for prefix, tag in (("text_encoder_2_", "te2"), ("text_encoder_", te1_tag)):
            if key.startswith(prefix):
                state[f"lora_{tag}_{key[len(prefix) :]}"] = state.pop(key)
                break
    return state


def _derive_keys(parts: list[str]) -> list[str]:
    out = []
    for index in range(3):
        new_parts = "_".join(parts[index:]).split(".")
        if len(new_parts) <= 1:
            continue
        end = len(new_parts) - 2 if len(new_parts) > 2 else len(new_parts) - 1
        out.append(".".join(new_parts[:end]) + ".weight")
    return out


def _find(mapping: dict[str, tuple[str, ...]], keys: list[str]) -> tuple[str, tuple[str, ...]] | None:
    for key in keys:
        if key in mapping:
            return key, mapping[key]
        parts = key.split(".")
        if parts[0] == "model":
            if len(parts) > 3 and parts[1] == "diffusion_model":
                stripped = ".".join(parts[2:])
                if stripped in mapping:
                    return stripped, mapping[stripped]
            if len(parts) > 2:
                stripped = ".".join(parts[1:])
                if stripped in mapping:
                    return stripped, mapping[stripped]
        elif parts[0] == "diffusion_model" and len(parts) > 2:
            stripped = ".".join(parts[1:])
            if stripped in mapping:
                return stripped, mapping[stripped]
    return None


def _split_up(up: np.ndarray, count: int) -> tuple[list[np.ndarray], bool]:
    """Rows of a fused ``up`` tensor per target (the Swift ``write`` for ``.O`` elements)."""
    if count == 1:
        return [up], False
    if up.ndim != 2:
        raise UnsupportedLoraFormatError("Fused 4-D LoRA tensors are not supported.", hint=COLAB_HINT)
    if up.shape[0] % count != 0:
        raise ShapeMismatchError(f"Cannot split {up.shape[0]} rows into {count} parts.", hint=COLAB_HINT)
    rows = up.shape[0] // count
    if is_diagonal_up(up, count):
        cols = up.shape[1] // count
        return [up[i * rows : (i + 1) * rows, i * cols : (i + 1) * cols] for i in range(count)], True
    return [up[i * rows : (i + 1) * rows] for i in range(count)], False


def _split_down(down: np.ndarray, count: int, diagonal: bool) -> list[np.ndarray]:
    if count == 1 or not diagonal:
        return [down] * count
    rows = down.shape[0] // count
    return [down[i * rows : (i + 1) * rows] for i in range(count)]


def convert_tables(
    st: SafeTensors, family: str, scale: float, ignore_unmatched: bool, ctx: ConversionContext
) -> dict[str, np.ndarray]:
    data = _mappings()
    resnets = [tuple(p) for p in data["resnets_partials"]]
    if family == "sd1":
        unet_table, fixed_table = data["sd1_unet"], {}
        partials = [tuple(p) for p in data["sd1_unet_partials"]]
        te1_map, te2_map = _clip_text(12), {}
    else:
        unet_table, fixed_table = data["sdxl_unet"], data["sdxl_unet_fixed"]
        partials = _sdxl_partials()
        te1_map = _clip_text(12)
        te2_map = _clip_text(32)
    unet_map = _expand_unet(_with_diffusers_names(unet_table, partials, resnets))
    fixed_map = _expand_unet(_with_diffusers_names(fixed_table, partials, resnets)) if fixed_table else {}
    te1_map, te2_map = _expand_text(te1_map), _expand_text(te2_map)

    state = _normalize_state(st.keys(), "te1" if family == "sdxl" else "te")
    # Every file key must be an up, down or alpha tensor (Draw Things drops the rest silently).
    stray = sorted(k for k in st.keys() if split_key(k) is None)
    if stray and not ignore_unmatched:
        raise UnsupportedLoraFormatError(
            f"{len(stray)} tensors are not plain LoRA up/down/alpha keys (for example {', '.join(stray[:4])}); "
            f"DoRA, bias and other extra keys are not supported. Pass --ignore-unmatched to skip them, "
            f"or {COLAB_HINT}"
        )
    if stray:
        ctx.log(f"warning: skipped {len(stray)} tensors that are not up/down/alpha keys (for example {stray[0]}).")
    for key in state:
        if (
            any(tag in key for tag in ("hada_", "lokr_", "::diff"))
            or key.endswith((".diff", ".diff_b", ".mid.weight"))
            or key in ("emb_params", "clip_g", "clip_l")
        ):
            raise UnsupportedLoraFormatError(
                f"{key}: LoHa, LoKr, Tucker, diff and embedding keys are not supported.", hint=COLAB_HINT
            )

    swap_te2 = False
    if family == "sdxl":

        def k_proj(tag: str) -> int | None:
            for key in sorted(state):
                if key.endswith("encoder_layers_0_self_attn_k_proj.lora_down.weight") and f"_{tag}_" in key:
                    return st.shape(state[key])[-1]
            return None

        te1, te2, te = k_proj("te1"), k_proj("te2"), k_proj("te")
        if te1 is not None and te2 is not None:
            swap_te2 = te2 != 1280 and te1 == 1280
        elif te2 is not None:
            swap_te2 = te2 == 768
        elif te1 == 1280 or te == 1280:
            swap_te2 = True

    ordered = sorted(state)

    def alpha_of(key: str) -> np.float32 | None:
        alpha_key = key[:-14] + "alpha"
        return scalar(st.read(state[alpha_key])) if alpha_key in state else None

    # The last (sorted) source key per table entry wins, like the Swift ``mappingUpKeys``.
    def last_by_entry(table: dict[str, tuple[str, ...]], suffix: str) -> dict[str, str]:
        found: dict[str, str] = {}
        for key in ordered:
            parts = key.split("_")
            if len(parts) <= 2:
                continue
            hit = _find(table, _derive_keys(parts))
            if hit is not None and key.endswith(suffix):
                found[hit[0]] = key
        return found

    up_for = last_by_entry(unet_map, "up.weight")
    down_for = last_by_entry(unet_map, "down.weight")

    out: dict[str, np.ndarray] = {}
    diagonal: set[str] = set()
    unmatched: list[str] = []

    def text_target(key: str, parts: list[str]) -> tuple[tuple[str, ...], bool] | None:
        te2 = parts[1] == "te2"
        table = te2_map if te2 != swap_te2 else te1_map
        hit = _find(table, _derive_keys(parts))
        if hit is None:
            return None
        return hit[1][:1], te2 != swap_te2

    consumed: set[str] = set()
    total = 2 * len(ordered)
    for index, key in enumerate(ordered):
        ctx.step(index, total)
        parts = key.split("_")
        if len(parts) <= 2 or not key.endswith("up.weight"):
            continue
        hit = _find(unet_map, _derive_keys(parts))
        fixed = False
        if hit is not None and hit[0] in consumed:
            continue
        if hit is None and fixed_map:
            hit, fixed = _find(fixed_map, _derive_keys(parts)), True
        if hit is not None:
            entry, names = hit
            real = state[up_for.get(entry, key)] if not fixed and entry in up_for else state[key]
            if not fixed:
                consumed.add(entry)
            up = st.read_f16(real)
            pieces, is_diag = _split_up(up, len(names))
            if is_diag:
                diagonal.add(entry)
            prefix = "unet_fixed" if fixed else "unet"
            alpha = alpha_of(key)
            for name, piece in zip(names, pieces, strict=True):
                out[f"__{prefix}__[{name}]__up__"] = scale_up(piece, alpha, scale, rank=up.shape[1])
            continue
        target = text_target(key, parts)
        if target is None:
            unmatched.append(key)
            continue
        names, is_te2 = target
        up = st.read_f16(state[key])
        prefix = "__te2__text_model__" if is_te2 else "__text_model__"
        out[f"{prefix}[{names[0]}]__up__"] = scale_up(up, alpha_of(key), scale, rank=up.shape[1])

    consumed.clear()
    for index, key in enumerate(ordered):
        ctx.step(len(ordered) + index, total)
        parts = key.split("_")
        if len(parts) <= 2 or not key.endswith("down.weight"):
            continue
        hit = _find(unet_map, _derive_keys(parts))
        fixed = False
        if hit is not None and hit[0] in consumed:
            continue
        if hit is None and fixed_map:
            hit, fixed = _find(fixed_map, _derive_keys(parts)), True
        if hit is not None:
            entry, names = hit
            real = state[down_for.get(entry, key)] if not fixed and entry in down_for else state[key]
            if not fixed:
                consumed.add(entry)
            down = scale_down(st.read_f16(real), scale)
            prefix = "unet_fixed" if fixed else "unet"
            for name, piece in zip(names, _split_down(down, len(names), entry in diagonal), strict=True):
                out[f"__{prefix}__[{name}]__down__"] = piece
            continue
        target = text_target(key, parts)
        if target is None:
            unmatched.append(key)
            continue
        names, is_te2 = target
        prefix = "__te2__text_model__" if is_te2 else "__text_model__"
        out[f"{prefix}[{names[0]}]__down__"] = scale_down(st.read_f16(state[key]), scale)

    for name, array in sorted(out.items()):
        if not name.endswith("__up__"):
            continue
        down = out.get(name[: -len("__up__")] + "__down__")
        if down is None:
            raise UnsupportedLoraFormatError(
                f"{name[:-6]}: has an up tensor but no down tensor in the file.", hint=COLAB_HINT
            )
        if array.ndim != down.ndim or array.shape[1] != down.shape[0]:
            raise ShapeMismatchError(
                f"{name[:-6]}: rank mismatch, up {array.shape} does not fit down {down.shape}.", hint=COLAB_HINT
            )
    for name in sorted(out):
        if name.endswith("__down__") and name[: -len("__down__")] + "__up__" not in out:
            raise UnsupportedLoraFormatError(
                f"{name[:-8]}: has a down tensor but no up tensor in the file.", hint=COLAB_HINT
            )
    if unmatched:
        sample = ", ".join(sorted(set(unmatched))[:4])
        message = f"{len(set(unmatched))} LoRA tensors have no place in the {family} layers (for example {sample})."
        if not ignore_unmatched:
            raise UnmatchedModulesError(
                f"{message} Pass --ignore-unmatched to skip them like Draw Things does, or {COLAB_HINT}",
                modules=tuple(sorted(set(unmatched))),
            )
        ctx.log(f"warning: {message} Skipped.")
    return out
