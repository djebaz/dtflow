"""Source key parsing: which module a tensor belongs to and what role it plays."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoraKey:
    """One source tensor: which module it belongs to and what it is."""

    module: str  # module path with every '.' turned into '_', prefixes stripped
    role: str  # "up", "down" or "alpha"
    key: str  # original key in the file


_ROLE_SUFFIXES: tuple[tuple[str, str], ...] = (
    (".lora_B.default.weight", "up"),
    (".lora_A.default.weight", "down"),
    (".lora_B.weight", "up"),
    (".lora_A.weight", "down"),
    (".lora.up.weight", "up"),
    (".lora.down.weight", "down"),
    (".lora_up.weight", "up"),
    (".lora_down.weight", "down"),
    ("::lora::0", "up"),
    ("::lora::1", "down"),
    (".alpha", "alpha"),
    ("::alpha", "alpha"),
)

_PREFIXES = (
    "base_model.model.",
    "base_model.",
    "diffusion_model.",
    "transformer.",
    "lora_unet__",
    "lora_unet_",
    "lora_transformer_",
)


def split_key(key: str) -> LoraKey | None:
    """Split a source key into (module, role). Returns None for keys that are not LoRA parts."""
    match = next(((s, r) for s, r in _ROLE_SUFFIXES if key.endswith(s)), None)
    if match is None:
        return None
    suffix, role = match
    module = key[: -len(suffix)]
    changed = True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            if module.startswith(prefix):
                module = module[len(prefix) :]
                changed = True
    return LoraKey(module.replace(".", "_"), role, key)
