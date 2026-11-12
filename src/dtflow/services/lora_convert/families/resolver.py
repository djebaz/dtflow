"""Engine for resolver families (Krea 2, Z Image, Flux 1).

A resolver maps a normalized module path to the Draw Things layer(s) it targets, or None when
the module is not recognised. Moved unchanged from the old single-file converter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from dtflow.services.lora_convert.context import ConversionContext
from dtflow.services.lora_convert.errors import (
    COLAB_HINT,
    ShapeMismatchError,
    UnmatchedModulesError,
    UnsupportedLoraFormatError,
)
from dtflow.services.lora_convert.keys import LoraKey, split_key
from dtflow.services.lora_convert.numeric import scalar, scale_down, scale_up, split_fused, split_fused_inputs
from dtflow.services.lora_convert.safetensors_io import SafeTensors

Resolver = Callable[[str], "Target | None"]


@dataclass(frozen=True)
class Target:
    prefix: str  # "dit"
    names: tuple[str, ...]  # NNC parameter names, e.g. ("t-to_q-0-0",)
    # Column offsets in the (fused) ``down`` tensor when the source layer fuses several INPUTS
    # (Flux 1 single block linear2). Every target then gets the whole ``up`` and its slice of ``down``.
    input_offsets: tuple[int, ...] | None = None
    # Row offsets in the fused ``up`` tensor when the parts differ in size (Flux 1 single block linear1);
    # None means equal parts.
    output_offsets: tuple[int, ...] | None = None
    # Exact shapes of the whole (fused) source tensors, when the family knows them:
    # ``up`` rows and ``down`` columns. None skips the check.
    up_rows: int | None = None
    down_cols: int | None = None


def run_resolver(
    st: SafeTensors, family: str, resolver: Resolver, scale: float, ctx: ConversionContext
) -> dict[str, np.ndarray]:
    parts: dict[str, dict[str, LoraKey]] = {}
    unknown: list[str] = []
    for key in st.keys():
        parsed = split_key(key)
        if parsed is None:
            unknown.append(key)
            continue
        roles = parts.setdefault(parsed.module, {})
        if parsed.role in roles:
            raise UnsupportedLoraFormatError(
                f"{key} and {roles[parsed.role].key} are both the {parsed.role} of the same module ({parsed.module}).",
                hint=COLAB_HINT,
            )
        roles[parsed.role] = parsed
    if unknown:
        sample = ", ".join(unknown[:5])
        raise UnsupportedLoraFormatError(
            f"{len(unknown)} tensors are not plain LoRA up/down/alpha keys (for example {sample}). "
            "LoHa, LoKr, bias and diff keys are not supported.",
            hint=COLAB_HINT,
        )

    out: dict[str, np.ndarray] = {}
    unmatched: list[str] = []
    modules = sorted(parts.items())
    for index, (module, roles) in enumerate(modules):
        ctx.step(index, len(modules))
        target = resolver(module)
        if target is None:
            unmatched.append(module)
            continue
        if "up" not in roles or "down" not in roles:
            raise UnsupportedLoraFormatError(f"{module}: needs both an up and a down tensor.")
        alpha = scalar(st.read(roles["alpha"].key)) if "alpha" in roles else None
        up = st.read_f16(roles["up"].key)
        down = st.read_f16(roles["down"].key)
        if up.ndim != 2 or down.ndim != 2 or up.shape[1] != down.shape[0]:
            raise ShapeMismatchError(
                f"{module}: unexpected shapes up {up.shape} and down {down.shape}.", hint=COLAB_HINT
            )
        if target.up_rows is not None and (up.shape[0] != target.up_rows or down.shape[1] != target.down_cols):
            raise ShapeMismatchError(
                f"{module}: expected up with {target.up_rows} rows and down with {target.down_cols} columns, "
                f"got up {up.shape} and down {down.shape}. A different model size or variant?",
                hint=COLAB_HINT,
            )
        if target.input_offsets is not None:
            pieces = split_fused_inputs(up, down, target.input_offsets)
        else:
            pieces = split_fused(up, down, len(target.names), target.output_offsets)
        for name, (piece_up, piece_down) in zip(target.names, pieces, strict=True):
            # The rank used for alpha scaling is the one of the whole fused tensor, as in Swift.
            out[f"__{target.prefix}__[{name}]__up__"] = scale_up(piece_up, alpha, scale, rank=up.shape[1])
            out[f"__{target.prefix}__[{name}]__down__"] = scale_down(piece_down, scale)
    if unmatched:
        sample = ", ".join(unmatched[:5])
        raise UnmatchedModulesError(
            f"{len(unmatched)} modules do not belong to the {family} layers this converter knows "
            f"(for example {sample}). The file may be for another family or train layers that are "
            "not ported yet.",
            hint=COLAB_HINT,
        )
    return out
