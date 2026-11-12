"""Z Image (Turbo) family."""

from __future__ import annotations

import re

from dtflow.services.lora_convert.families.resolver import Target, run_resolver
from dtflow.services.lora_convert.registry import FamilySpec, register


_ZIMAGE_BLOCK = re.compile(r"^(layers|noise_refiner|context_refiner)_(\d+)_(\w+)$")
_ZIMAGE_COUNTS = {"layers": 30, "noise_refiner": 2, "context_refiner": 2}
_ZIMAGE_LEAVES = {
    "attention_to_q": "q",
    "attention_to_k": "k",
    "attention_to_v": "v",
    "attention_to_out_0": "o",
    "attention_to_out": "o",
    "attention_out": "o",
    "feed_forward_w1": "ffn_gate_proj",
    "feed_forward_w3": "ffn_up_proj",
    "feed_forward_w2": "ffn_down_proj",
}
_ZIMAGE_FFN = {"ffn_gate_proj", "ffn_up_proj", "ffn_down_proj"}


def resolve_zimage(module: str) -> Target | None:
    """Z Image (Turbo): 30 main layers plus 2 noise refiner and 2 context refiner blocks.

    Names follow the Swift model: ``t-<group>_<layer>-<block index>-0`` with an empty group
    for the main layers; the three feed forward layers live in a named sub-model
    (``t-ffn-<i>-ffn_down_proj-0-0``). Main layers match the Swift output; the refiner blocks
    follow the same rule but were not part of the reference LoRA. Fused sources (``attention.qkv`` and ``adaLN_modulation.0``) map
    to several targets and are split by ``split_fused``.
    """
    match = _ZIMAGE_BLOCK.match(module)
    if match is None:
        return None
    group, index, leaf = match.group(1), int(match.group(2)), match.group(3)
    if index >= _ZIMAGE_COUNTS[group]:
        return None
    prefix = "" if group == "layers" else f"{group}_"

    def name(layer: str) -> str:
        if layer in _ZIMAGE_FFN:
            # The feed forward is its own named sub-model: t-<ffn model>-<block>-<layer>-0-0
            # (checked against the Swift output for the 30 main layers).
            return f"t-{prefix}ffn-{index}-{prefix}{layer}-0-0"
        return f"t-{prefix}{layer}-{index}-0"

    if leaf in _ZIMAGE_LEAVES:
        return Target("dit", (name(_ZIMAGE_LEAVES[leaf]),))
    if leaf == "attention_qkv":
        return Target("dit", (name("q"), name("k"), name("v")))
    if leaf == "adaLN_modulation_0" and group != "context_refiner":
        return Target("dit", tuple(name(f"ada_ln_{k}") for k in range(4)))
    return None


register(
    FamilySpec(
        key="zimage",
        label="Z Image",
        exact_architecture="z_image",
        status="verified",
        experimental=False,
        kind="resolver",
        run=lambda st, request, ctx: run_resolver(st, "zimage", resolve_zimage, request.scale, ctx),
        notes="Swift-compared and smoked on DT+",
    )
)
