"""Krea 2 family: 28 DiT blocks plus the text fusion adapter."""

from __future__ import annotations

import re

from dtflow.services.lora_convert.families.resolver import Target, run_resolver
from dtflow.services.lora_convert.registry import FamilySpec, register


_KREA2_ATTN = {
    "to_q": "to_q",
    "wq": "to_q",
    "to_k": "to_k",
    "wk": "to_k",
    "to_v": "to_v",
    "wv": "to_v",
    "to_gate": "to_gate",
    "gate": "to_gate",
    "to_out_0": "to_out",
    "to_out": "to_out",
    "wo": "to_out",
}
_KREA2_FF = ("gate", "up", "down")

_KREA2_BLOCK = re.compile(r"^(?:transformer_)?blocks_(\d+)_(attn|ff|mlp)_(\w+)$")
_KREA2_FUSION = re.compile(r"^(?:text_fusion|txtfusion)_(layerwise|refiner)_blocks_(\d+)_(attn|ff|mlp)_(\w+)$")


# Non-block layers, normalized source module -> Draw Things names (checked against the Swift importer).
_KREA2_FIXED: dict[str, tuple[str, ...]] = {
    "img_in": ("t-img_in-0-0",),
    "final_layer_linear": ("t-final_linear-0-0",),
    "last_linear": ("t-final_linear-0-0",),
    "text_fusion_projector": ("t-text_fusion_projector-0-0",),
    "txtfusion_projector": ("t-text_fusion_projector-0-0",),
    "time_embed_linear_1": ("t-time_embed_linear_1-0-0",),
    "time_embed_linear_2": ("t-time_embed_linear_2-0-0",),
    "time_mod_proj": tuple(f"t-time_mod_proj_{i}-0-0" for i in range(6)),
    "txt_in_linear_1": ("t-linear_1-0-0",),
    "txt_in_linear_2": ("t-linear_2-0-0",),
    # Names of the same layers in the original model layout (Sequential indices), seen in LoRAs from other trainers.
    "first": ("t-img_in-0-0",),
    "tmlp_0": ("t-time_embed_linear_1-0-0",),
    "tmlp_2": ("t-time_embed_linear_2-0-0",),
    "tproj_1": tuple(f"t-time_mod_proj_{i}-0-0" for i in range(6)),
    "txtmlp_1": ("t-linear_1-0-0",),
    "txtmlp_3": ("t-linear_2-0-0",),
}


def _krea2_leaf(kind: str, leaf: str) -> str | None:
    if kind == "attn":
        return _KREA2_ATTN.get(leaf)
    return leaf if leaf in _KREA2_FF else None


def resolve_krea2(module: str) -> Target | None:
    """Krea 2 DiT blocks (28) and the text fusion adapter (2 layerwise + 2 refiner blocks).

    Parameter names follow the Swift model: ``t-<layer name>-<block index>-0`` where the
    layer name carries the group prefix (``text_fusion_layerwise_attn_to_q`` and so on).
    Also the layers outside the blocks (time embedding, text projection, image in/out, text input),
    whose names and the six way split of ``time_mod_proj`` were taken from the Swift importer's
    output (``lora-convert`` oracle), not from the source: ``t-<layer>-0-0``.
    """
    fixed = _KREA2_FIXED.get(module)
    if fixed is not None:
        return Target("dit", fixed)
    match = _KREA2_BLOCK.match(module)
    if match:
        index, kind, leaf = int(match.group(1)), match.group(2), match.group(3)
        name = _krea2_leaf(kind, leaf)
        if name is None or index >= 28:
            return None
        return Target("dit", (f"t-{name}-{index}-0",))
    match = _KREA2_FUSION.match(module)
    if match:
        group, index = match.group(1), int(match.group(2))
        kind, leaf = match.group(3), match.group(4)
        name = _krea2_leaf(kind, leaf)
        if name is None or index >= 2:
            return None
        sub = "attn" if kind == "attn" else "ff"
        return Target("dit", (f"t-text_fusion_{group}_{sub}_{name}-{index}-0",))
    return None


register(
    FamilySpec(
        key="krea2",
        label="Krea 2",
        exact_architecture="krea_2",
        status="verified",
        experimental=False,
        kind="resolver",
        run=lambda st, request, ctx: run_resolver(st, "krea2", resolve_krea2, request.scale, ctx),
        notes="Swift-compared and smoked on DT+",
    )
)
