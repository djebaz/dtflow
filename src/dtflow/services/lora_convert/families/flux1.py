"""Flux 1 (dev/schnell) family."""

from __future__ import annotations

import re
from dataclasses import replace

from dtflow.services.lora_convert.families.resolver import Target, run_resolver
from dtflow.services.lora_convert.registry import FamilyCode, register


# Flux 1 (dev/schnell): 19 double blocks then 38 single blocks, hidden size 3072. Draw Things numbers the
# blocks in one sequence, so single block j is block 19 + j. Names follow Flux1.swift / Flux1Fixed:
# ``t-<layer>-<block>-0`` with c_* (text stream) and x_* (image stream) layers.
FLUX1_DOUBLE = 19
FLUX1_SINGLE = 38
FLUX1_HIDDEN = 3072

_FLUX1_DOUBLE = re.compile(r"^(?:double_blocks|transformer_blocks)_(\d+)_(\w+)$")
_FLUX1_SINGLE = re.compile(r"^(?:single_blocks|single_transformer_blocks)_(\d+)_(\w+)$")
# leaf -> (stream, layer names) for the double blocks; original (BFL) names first, then diffusers.
_FLUX1_DOUBLE_LEAVES: dict[str, tuple[str, ...]] = {
    "img_attn_qkv": ("t-x_q", "t-x_k", "t-x_v"),
    "txt_attn_qkv": ("t-c_q", "t-c_k", "t-c_v"),
    "img_attn_proj": ("t-x_o",),
    "txt_attn_proj": ("t-c_o",),
    "img_mlp_0": ("t-x_linear1",),
    "img_mlp_2": ("t-x_out_proj",),
    "txt_mlp_0": ("t-c_linear1",),
    "txt_mlp_2": ("t-c_out_proj",),
    "img_mod_lin": tuple(f"t-x_ada_ln_{i}" for i in range(6)),
    "txt_mod_lin": tuple(f"t-context_ada_ln_{i}" for i in range(6)),
    "attn_to_q": ("t-x_q",),
    "attn_to_k": ("t-x_k",),
    "attn_to_v": ("t-x_v",),
    "attn_add_q_proj": ("t-c_q",),
    "attn_add_k_proj": ("t-c_k",),
    "attn_add_v_proj": ("t-c_v",),
    "attn_to_out_0": ("t-x_o",),
    "attn_to_add_out": ("t-c_o",),
    "ff_net_0_proj": ("t-x_linear1",),
    "ff_net_2": ("t-x_out_proj",),
    "ff_context_net_0_proj": ("t-c_linear1",),
    "ff_context_net_2": ("t-c_out_proj",),
    "norm1_linear": tuple(f"t-x_ada_ln_{i}" for i in range(6)),
    "norm1_context_linear": tuple(f"t-context_ada_ln_{i}" for i in range(6)),
}
_FLUX1_SINGLE_LEAVES: dict[str, tuple[str, ...]] = {
    "linear1": ("t-x_q", "t-x_k", "t-x_v", "t-x_linear1"),
    "modulation_lin": tuple(f"t-x_ada_ln_{i}" for i in range(3)),
    "attn_to_q": ("t-x_q",),
    "attn_to_k": ("t-x_k",),
    "attn_to_v": ("t-x_v",),
    "proj_mlp": ("t-x_linear1",),
    "norm_linear": tuple(f"t-x_ada_ln_{i}" for i in range(3)),
}


def _flux1_dims(names: tuple[str, ...], fused_inputs: bool) -> tuple[int, int]:
    """(up rows, down columns) of the source tensor that feeds ``names`` (hidden size 3072)."""
    rows = cols = 0
    first_rows = first_cols = 0
    for i, name in enumerate(names):
        base = name.rsplit("-", 2)[0].removeprefix("t-")
        out_dim, in_dim = FLUX1_HIDDEN, FLUX1_HIDDEN
        if base.endswith("linear1"):
            out_dim = 4 * FLUX1_HIDDEN
        elif base.endswith("out_proj"):
            in_dim = 4 * FLUX1_HIDDEN
        if i == 0:
            first_rows, first_cols = out_dim, in_dim
        rows += out_dim
        cols += in_dim
    return (first_rows, cols) if fused_inputs else (rows, first_cols)


def _with_flux1_shapes(target: Target) -> Target:
    rows, cols = _flux1_dims(target.names, target.input_offsets is not None)
    return replace(target, up_rows=rows, down_cols=cols)


def resolve_flux1(module: str) -> Target | None:
    target = _resolve_flux1(module)
    return None if target is None else _with_flux1_shapes(target)


def _resolve_flux1(module: str) -> Target | None:
    """Flux 1 blocks, original (``double_blocks.N.img_attn.qkv``) and diffusers (``transformer_blocks.N.attn.to_q``) names."""
    match = _FLUX1_DOUBLE.match(module)
    if match:
        index, leaf = int(match.group(1)), match.group(2)
        names = _FLUX1_DOUBLE_LEAVES.get(leaf)
        if names is None or index >= FLUX1_DOUBLE:
            return None
        return Target("dit", tuple(f"{name}-{index}-0" for name in names))
    match = _FLUX1_SINGLE.match(module)
    if match:
        index, leaf = int(match.group(1)), match.group(2)
        if index >= FLUX1_SINGLE:
            return None
        block = FLUX1_DOUBLE + index
        if leaf in ("linear2", "proj_out"):
            # linear2 fuses two inputs: attention output (3072 columns) and the MLP output (12288).
            return Target(
                "dit",
                (f"t-x_o-{block}-0", f"t-x_out_proj-{block}-0"),
                input_offsets=(0, FLUX1_HIDDEN),
            )
        names = _FLUX1_SINGLE_LEAVES.get(leaf)
        if names is None:
            return None
        offsets = (0, FLUX1_HIDDEN, 2 * FLUX1_HIDDEN, 3 * FLUX1_HIDDEN) if leaf == "linear1" else None
        return Target("dit", tuple(f"{name}-{block}-0" for name in names), output_offsets=offsets)
    return None


register(
    FamilyCode(
        key="flux1",
        kind="resolver",
        run=lambda st, request, ctx: run_resolver(st, "flux1", resolve_flux1, request.scale, ctx),
        notes="Swift-compared on three files; generation effect confirmed on DT+ by the owner (2026-11-13)",
    )
)
