"""Optional safetensors structural diagnostics for the LoRA conversion CLI.

Header-only tensor inspection, apart from scalar alpha values. The report describes
mapping recognition and common shape issues; it does not prove numerical compatibility
with Draw Things, and it never guesses a source model family.
"""

from __future__ import annotations

import math
from collections import Counter
from functools import cache
from pathlib import Path

from dtflow.services.lora_convert.keys import split_key
from dtflow.services.lora_convert.safetensors_io import SafeTensors


def _category(module: str) -> str:
    """Group normalized source names for exploration, not model-architecture validation."""
    name = module.casefold()
    if any(part in name for part in ("attn", "attention", "to_q", "to_k", "to_v", "to_out", "qkv")):
        return "attention"
    if any(part in name for part in ("ffn", "feed_forward", "mlp", "_ff_", "_linear1", "_linear2")):
        return "feed_forward"
    if any(part in name for part in ("ada_ln", "adaln", "modulation", "time_embed", "time_mod", "norm")):
        return "modulation_or_norm"
    if any(part in name for part in ("text", "txt", "clip", "context")):
        return "text"
    if any(part in name for part in ("img_in", "image", "final_layer", "last_linear")):
        return "image_or_output"
    return "other"


@cache
def _table_maps(family: str) -> tuple[dict, dict, dict, dict]:
    """Build table lookups with the same helpers as the SD1/SDXL conversion engine."""
    from dtflow.services.lora_convert.families import tables

    data = tables._mappings()
    resnets = [tuple(part) for part in data["resnets_partials"]]
    if family == "sd1":
        unet, fixed = data["sd1_unet"], {}
        partials = [tuple(part) for part in data["sd1_unet_partials"]]
        te1, te2 = tables._clip_text(12), {}
    else:
        unet, fixed = data["sdxl_unet"], data["sdxl_unet_fixed"]
        partials = tables._sdxl_partials()
        te1, te2 = tables._clip_text(12), tables._clip_text(32)
    unet_map = tables._expand_unet(tables._with_diffusers_names(unet, partials, resnets))
    fixed_map = tables._expand_unet(tables._with_diffusers_names(fixed, partials, resnets)) if fixed else {}
    return unet_map, fixed_map, tables._expand_text(te1), tables._expand_text(te2)


def _table_matches(keys: list[str], family: str) -> set[str]:
    """Estimate source modules recognized by the table key lookups, ignoring weight values."""
    from dtflow.services.lora_convert.families import tables

    lookups = _table_maps(family)
    state = tables._normalize_state(keys, "te1" if family == "sdxl" else "te")
    matched: set[str] = set()
    for normalized, original in state.items():
        if not normalized.endswith((".lora_up.weight", ".lora_down.weight")):
            continue
        parts = normalized.split("_")
        if len(parts) <= 2:
            continue
        candidates = tables._derive_keys(parts)
        if any(tables._find(mapping, candidates) for mapping in lookups):
            parsed = split_key(original)
            if parsed is not None:
                matched.add(parsed.module)
    return matched


def _resolver_matches(modules: set[str], family: str) -> tuple[set[str], dict[str, object]]:
    """Use the actual family resolver without loading tensor weights."""
    if family == "krea2":
        from dtflow.services.lora_convert.families.krea2 import resolve_krea2 as resolve
    elif family == "zimage":
        from dtflow.services.lora_convert.families.zimage import resolve_zimage as resolve
    else:
        from dtflow.services.lora_convert.families.flux1 import resolve_flux1 as resolve

    targets = {module: resolve(module) for module in modules}
    return {module for module, target in targets.items() if target is not None}, targets


def analyze_source(path: Path, family: str) -> dict:
    """Summarize source tensor structure and selected-family mapping coverage.

    Counts are per source module. Alpha distribution is read from scalar alpha
    tensors only; all other statistics use the safetensors header.
    """
    with SafeTensors(path) as source:
        keys = source.keys()
        parts: dict[str, dict[str, str]] = {}
        duplicates: list[str] = []
        unsupported: list[str] = []
        dtype_counts: Counter[str] = Counter()
        for key in keys:
            dtype_counts[str(source.entries[key]["dtype"])] += 1
            parsed = split_key(key)
            if parsed is None:
                unsupported.append(key)
                continue
            roles = parts.setdefault(parsed.module, {})
            if parsed.role in roles:
                duplicates.append(parsed.module + ": " + parsed.role)
            roles[parsed.role] = key

        modules = set(parts)
        rank_counts: Counter[str] = Counter()
        alpha_counts: Counter[str] = Counter()
        categories = Counter(_category(module) for module in modules)
        shape_issues: list[dict[str, str]] = []
        for module, roles in sorted(parts.items()):
            up_key, down_key = roles.get("up"), roles.get("down")
            if not up_key or not down_key:
                shape_issues.append({"module": module, "issue": "missing_up_or_down"})
            else:
                up, down = source.shape(up_key), source.shape(down_key)
                if len(up) > 1 and len(down) > 0:
                    rank_counts[str(down[0])] += 1
                if len(up) < 2 or len(down) < 2 or up[1] != down[0]:
                    shape_issues.append({"module": module, "issue": "incompatible_rank_or_dimensions"})
                elif family in ("krea2", "zimage", "flux1") and (len(up) != 2 or len(down) != 2):
                    shape_issues.append({"module": module, "issue": "resolver_requires_2d_tensors"})
            if "alpha" in roles:
                alpha_key = roles["alpha"]
                shape = source.shape(alpha_key)
                if math.prod(shape) != 1:
                    shape_issues.append({"module": module, "issue": "alpha_not_scalar"})
                else:
                    try:
                        value = float(source.read(alpha_key).reshape(-1)[0])
                        alpha_counts[format(value, ".8g")] += 1
                    except (ValueError, TypeError, KeyError) as error:
                        shape_issues.append({"module": module, "issue": "alpha_unreadable: " + str(error)})
        for duplicate in duplicates:
            shape_issues.append({"module": duplicate, "issue": "duplicate_role"})

        if family in ("krea2", "zimage", "flux1"):
            matched, targets = _resolver_matches(modules, family)
            method = "resolver_key_lookup"
            for module in sorted(matched):
                target = targets[module]
                roles = parts[module]
                if target.up_rows is not None and "up" in roles and "down" in roles:
                    up = source.shape(roles["up"])
                    down = source.shape(roles["down"])
                    if len(up) >= 2 and len(down) >= 2 and (
                        up[0] != target.up_rows or down[1] != target.down_cols
                    ):
                        shape_issues.append({"module": module, "issue": "target_shape_mismatch"})
        elif family in ("sd1", "sdxl"):
            matched = _table_matches(keys, family)
            method = "table_key_lookup_estimate"
        else:
            matched = set()
            method = "unavailable"
        unmatched = sorted(modules - matched)
        coverage = {
            "method": method,
            "source_modules": len(modules),
            "matched_modules": len(matched),
            "unmatched_modules": unmatched,
            "coverage_percent": round(100 * len(matched) / len(modules), 2) if modules else None,
        }
        return {
            "source_bytes": path.stat().st_size,
            "source_tensors": len(keys),
            "source_modules": len(modules),
            "dtype_distribution": dict(sorted(dtype_counts.items())),
            "rank_distribution": dict(sorted(rank_counts.items(), key=lambda pair: int(pair[0]))),
            "alpha_distribution": dict(sorted(alpha_counts.items())),
            "alpha_modules": sum("alpha" in roles for roles in parts.values()),
            "module_coverage": coverage,
            "layer_categories": dict(sorted(categories.items())),
            "shape_issues": shape_issues,
            "unsupported_tensor_keys": sorted(unsupported),
            "limitations": (
                "Name-based layer categories and header-only shape checks are diagnostic, "
                "not full output validation. Table coverage is estimated. "
                "The selected --family is not automatically detected."
            ),
        }
