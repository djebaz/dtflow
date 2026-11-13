"""Header-only detection of the LoRA family of a ``.safetensors`` source (#402).

Reads only the safetensors header: tensor names and shapes, never weights, never the filename and
never ``__metadata__``. A family is returned only when several independent structural signals agree:

* every recognised LoRA module maps under that family's own resolver or tables,
* a family specific group is present (or two independent groups when no width can be checked),
* the input width of the q/k/v projections (and Z Image feed-forward w1/w3) corroborates it,
* nothing contradicts it (other widths, the other SD text encoder namespace).

Anything weaker stays unknown or ambiguous so the owner chooses. SD 2.x (cross-attention width 1024)
and the SDXL refiner (1280) are explicit negatives. The detector never feeds source keys to the
converted ``.ckpt`` detector (``lora_family.py``); the two use different naming and fused shapes.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dtflow.services.lora_convert.errors import SourceReadError
from dtflow.services.lora_convert.keys import split_key
from dtflow.services.lora_convert.safetensors_io import SafeTensors

log = logging.getLogger(__name__)

_MAX_EVIDENCE = 6

# Hidden width at the input of the q/k/v projections (source ``lora_down`` second dimension).
_KREA2_WIDTH = 6144
_ZIMAGE_WIDTH = 3840
_SD1_CROSS = 768
_SDXL_CROSS = 2048
_SD2_CROSS = 1024
_SDXL_REFINER_CROSS = 1280


@dataclass(frozen=True, slots=True)
class FamilyDetection:
    """Outcome of one detection. ``family`` is a converter key and is set only when ``outcome`` is "detected"."""

    outcome: str  # "detected" | "ambiguous" | "unknown" | "unreadable"
    family: str | None = None
    candidates: tuple[str, ...] = ()
    reason: str = ""  # one short line for the Prepare row
    evidence: tuple[str, ...] = ()  # bounded structural facts, no tensor names

    def as_report(self, selected: str | None = None) -> dict:
        """JSON-ready form for the CLI analysis report."""
        report = {
            "outcome": self.outcome,
            "family": self.family,
            "candidates": list(self.candidates),
            "candidate_count": len(self.candidates),
            "reason": self.reason,
            "evidence": list(self.evidence),
        }
        if selected is not None:
            report["matches_selected_family"] = self.family == selected if self.family else None
        return report


@dataclass
class _Verdict:
    family: str
    ok: bool = False
    evidence: list[str] | None = None
    note: str | None = None  # a specific reason when a known unsupported variant was recognised


def _label(key: str) -> str:
    from dtflow.services.lora_convert.registry import lookup

    return lookup(key).label


def _dims(modules: dict[str, dict[str, tuple[int, ...]]], names=None) -> set[int]:
    """Every dimension of the up/down tensors of ``names`` (all modules by default)."""
    chosen = modules if names is None else {name: modules[name] for name in names}
    return {int(d) for roles in chosen.values() for role in ("up", "down") for d in roles.get(role, ())}


def _missing(label: str, what: str) -> str:
    return (
        f"{label} structure, but {what}: the converted-output check would not accept it, so choose the family yourself"
    )


def _dim_in(shape: tuple[int, ...] | None) -> int | None:
    """Input width of a LoRA: second dimension of ``lora_down``."""
    return int(shape[1]) if shape is not None and len(shape) >= 2 else None


@dataclass(frozen=True)
class _ResolverRule:
    key: str
    resolve: Callable[[str], object | None]
    tag: Callable[[str], str | None]  # family specific group of a module, None when generic
    width_module: Callable[[str], bool]  # input projections whose input is the hidden width
    width: int | None  # expected hidden width; None when the resolver checks exact shapes itself
    # Parts the converted-output check (lora_family.py) or a look-alike model needs: a note when one is missing.
    require: Callable[[dict[str, dict[str, tuple[int, ...]]]], str | None]


def _krea2_rule() -> _ResolverRule:
    from dtflow.services.lora_convert.families import krea2

    generic_fixed = {"img_in", "final_layer_linear", "last_linear"}
    qkv = re.compile(r"^(?:transformer_)?blocks_\d+_attn_(?:to_q|wq|to_k|wk|to_v|wv)$")

    def tag(module: str) -> str | None:
        if krea2._KREA2_FUSION.match(module) or module.startswith(("text_fusion_", "txtfusion_")):
            return "text fusion"
        if module in krea2._KREA2_FIXED:
            return None if module in generic_fixed else "krea layers outside the blocks"
        match = krea2._KREA2_BLOCK.match(module)
        if match is None:
            return None
        if match.group(2) == "attn" and krea2._krea2_leaf("attn", match.group(3)) == "to_gate":
            return "attention gate"
        if match.group(2) in ("ff", "mlp"):
            return "feed-forward gate/up/down"
        return None

    main_attn = re.compile(r"^(?:transformer_)?blocks_\d+_attn_")

    def require(modules: dict[str, dict[str, tuple[int, ...]]]) -> str | None:
        # Same parts as the converted check: text fusion, block attention and the 6144/1536/16384 widths.
        if not any(tag(m) == "text fusion" for m in modules):
            return _missing("Krea 2", "no text fusion layers")
        if not any(main_attn.match(m) for m in modules):
            return _missing("Krea 2", "no block attention layers")
        if not {6144, 1536, 16384} <= _dims(modules):
            return _missing("Krea 2", "not all of its 6144, 1536 and 16384 widths are present")
        return None

    return _ResolverRule("krea2", krea2.resolve_krea2, tag, lambda m: bool(qkv.match(m)), _KREA2_WIDTH, require)


def _zimage_rule() -> _ResolverRule:
    from dtflow.services.lora_convert.families import zimage

    qkv_w13 = re.compile(
        r"^(?:layers|noise_refiner|context_refiner)_\d+_(?:attention_(?:to_q|to_k|to_v|qkv)|feed_forward_(?:w1|w3))$"
    )

    def tag(module: str) -> str | None:
        if module.startswith(("noise_refiner_", "context_refiner_")):
            return "refiner blocks"
        if "feed_forward_" in module:
            return "feed-forward"
        if module.endswith("adaLN_modulation_0"):
            return "adaLN modulation"
        return None

    def require(modules: dict[str, dict[str, tuple[int, ...]]]) -> str | None:
        # Same parts as the converted check: main-layer feed-forward with 3840/10240 plus attention or modulation.
        ffn = [m for m in modules if m.startswith("layers_") and "_feed_forward_" in m]
        if not ffn:
            return _missing("Z Image", "no feed-forward layers")
        if not {3840, 10240} <= _dims(modules, ffn):
            return _missing("Z Image", "its feed-forward widths 3840 and 10240 are not both present")
        if not any(
            m.startswith("layers_") and ("_attention_" in m or m.endswith("adaLN_modulation_0")) for m in modules
        ):
            return _missing("Z Image", "no attention or modulation layers")
        return None

    return _ResolverRule("zimage", zimage.resolve_zimage, tag, lambda m: bool(qkv_w13.match(m)), _ZIMAGE_WIDTH, require)


def _flux1_rule() -> _ResolverRule:
    from dtflow.services.lora_convert.families import flux1

    # Diffusers attention names that Krea 2 also resolves: not distinctive on their own.
    shared = re.compile(r"^transformer_blocks_\d+_attn_(?:to_q|to_k|to_v|to_out_0)$")
    width_leaves = (
        "img_attn_qkv|txt_attn_qkv|attn_to_q|attn_to_k|attn_to_v|attn_add_q_proj|attn_add_k_proj|attn_add_v_proj"
        "|linear1|proj_mlp"
    )
    qkv = re.compile(
        rf"^(?:double_blocks|transformer_blocks|single_blocks|single_transformer_blocks)_\d+_(?:{width_leaves})$"
    )

    def tag(module: str) -> str | None:
        if module.startswith(("single_blocks_", "single_transformer_blocks_")):
            return "single blocks"
        if shared.match(module):
            return None
        return "double blocks"

    # Names only Flux 1 uses; Hunyuan Video shares the other double/single block names and the same shapes.
    unique = re.compile(
        r"^(?:double_blocks_\d+_(?:img_mlp_[02]|txt_mlp_[02]|img_mod_lin|txt_mod_lin)|single_blocks_\d+_modulation_lin)$"
    )
    double_index = re.compile(r"^(?:double_blocks|transformer_blocks)_(\d+)_")
    single_index = re.compile(r"^(?:single_blocks|single_transformer_blocks)_(\d+)_")

    def require(modules: dict[str, dict[str, tuple[int, ...]]]) -> str | None:
        if any(unique.match(m) for m in modules):
            return None
        double = {int(g.group(1)) for m in modules if (g := double_index.match(m))}
        single = {int(g.group(1)) for m in modules if (g := single_index.match(m))}
        if len(double) == flux1.FLUX1_DOUBLE and len(single) == flux1.FLUX1_SINGLE:
            return None
        return (
            "Flux 1 structure, but nothing separates it from Hunyuan Video (shared names and shapes): "
            "choose the family yourself"
        )

    return _ResolverRule("flux1", flux1.resolve_flux1, tag, lambda m: bool(qkv.match(m)), None, require)


def _check_resolver(rule: _ResolverRule, modules: dict[str, dict[str, tuple[int, ...]]]) -> _Verdict:
    verdict = _Verdict(rule.key)
    label = _label(rule.key)
    unmapped = shape_off = 0
    tags: set[str] = set()
    width_seen: Counter[int] = Counter()
    for module, roles in modules.items():
        target = rule.resolve(module)
        if target is None:
            unmapped += 1
            continue
        up, down = roles.get("up"), roles.get("down")
        if up is None or down is None or len(up) != 2 or len(down) != 2 or up[1] != down[0]:
            shape_off += 1
            continue
        rows, cols = getattr(target, "up_rows", None), getattr(target, "down_cols", None)
        if rows is not None and (up[0] != rows or down[1] != cols):
            shape_off += 1  # Flux 1 knows the exact source shapes
            continue
        if (found := rule.tag(module)) is not None:
            tags.add(found)
        if rule.width_module(module):
            width_seen[down[1]] += 1
    if unmapped or shape_off:
        return verdict  # not this family; every module has to map with consistent shapes
    expected = rule.width
    if expected is not None and any(width != expected for width in width_seen):
        return verdict
    corroborated = bool(width_seen)
    # Z Image's layers_*/refiner namespace is already unique among the supported families.
    needs_tag = rule.key != "zimage"
    if corroborated:
        enough = bool(tags) or not needs_tag
    else:
        enough = len(tags) >= 2
    if not enough:
        return verdict
    missing = rule.require(modules)
    if missing:
        verdict.note = missing
        return verdict
    verdict.ok = True
    verdict.evidence = [f"all {len(modules)} modules map to {label}"]
    if tags:
        verdict.evidence.append("groups: " + ", ".join(sorted(tags)))
    if corroborated:
        width = next(iter(width_seen)) if rule.width is None else rule.width
        verdict.evidence.append(f"q/k/v input width {width} ({sum(width_seen.values())} modules)")
    else:
        verdict.evidence.append("no q/k/v input projection to check the width")
    return verdict


def _check_sd(family: str, keys: list[str], modules: dict[str, dict[str, tuple[int, ...]]]) -> _Verdict:
    from dtflow.services.lora_convert.analysis import _table_matches

    verdict = _Verdict(family)
    label = _label(family)
    widths: Counter[int] = Counter()
    for module, roles in modules.items():
        if "attn2_to_k" in module or "attn2_to_v" in module:
            width = _dim_in(roles.get("down"))
            if width is not None:
                widths[width] += 1
    has_te2 = any("lora_te2_" in key or "text_encoder_2" in key for key in keys)
    has_te1 = any("lora_te1_" in key for key in keys)
    has_te_plain = any(key.startswith("lora_te_") for key in keys)
    if not widths:
        return verdict  # no cross-attention evidence: text encoder or partial files stay unknown
    if set(widths) == {_SD2_CROSS}:
        verdict.note = "Cross-attention width 1024 looks like SD 2.x, which is not supported"
        return verdict
    if set(widths) == {_SDXL_REFINER_CROSS}:
        verdict.note = "Cross-attention width 1280 looks like the SDXL refiner, which is not supported"
        return verdict
    expected = _SD1_CROSS if family == "sd1" else _SDXL_CROSS
    if set(widths) != {expected}:
        return verdict
    if family == "sd1" and (has_te1 or has_te2):
        return verdict
    if family == "sdxl" and has_te_plain and not (has_te1 or has_te2):
        return verdict
    if _table_matches(keys, family) != set(modules):
        return verdict
    # The same parts the converted-output check needs: text encoder tensors and the UNet widths.
    text = [m for m in modules if "text_model_" in m]
    second = [m for m in text if "te2_" in m or "text_encoder_2_" in m]
    first = [m for m in text if m not in second]
    unet_dims = _dims(modules, [m for m in modules if m not in text])
    first_dims, second_dims = _dims(modules, first), _dims(modules, second)
    if family == "sd1":
        complete = (
            _SD1_CROSS in first_dims
            and {320, 640, 1280, 768} <= unet_dims
            and not ({1024, 2048} & unet_dims)
            and not ({1024, 1280} & first_dims)
            and not second
        )
    else:
        complete = (
            _SD1_CROSS in first_dims
            and 1280 in second_dims
            and {640, 1280, 2048} <= unet_dims
            and not ({384, 1536} & unet_dims)
        )
    if not complete:
        verdict.note = _missing(label, "it lacks text encoder tensors or part of the UNet widths")
        return verdict
    verdict.ok = True
    verdict.evidence = [
        f"all {len(modules)} modules map to {label} tables",
        f"cross-attention width {expected} ({sum(widths.values())} modules)",
        "text encoder tensors present" if family == "sd1" else "both text encoders present",
    ]
    return verdict


def _detect(source: SafeTensors) -> FamilyDetection:
    keys = source.keys()
    modules: dict[str, dict[str, tuple[int, ...]]] = {}
    other = 0
    for key in keys:
        parsed = split_key(key)
        if parsed is None:
            other += 1
            continue
        modules.setdefault(parsed.module, {})[parsed.role] = source.shape(key)
    modules = {module: roles for module, roles in modules.items() if "up" in roles or "down" in roles}
    if not modules:
        return FamilyDetection("unknown", reason="No LoRA up/down tensors found in this file")
    if other:
        # LoHa, LoKr, DoRA, bias or diff tensors beside plain LoRA ones: the converter refuses them, so no family
        # is suggested and its own error stays the meaningful one.
        return FamilyDetection(
            "unknown",
            reason="Contains tensors that are not plain LoRA up/down/alpha (LoHa, LoKr, bias or diff); choose the family",
            evidence=(f"{other} unsupported tensors",),
        )

    verdicts = [
        _check_resolver(_krea2_rule(), modules),
        _check_resolver(_zimage_rule(), modules),
        _check_resolver(_flux1_rule(), modules),
        _check_sd("sd1", keys, modules),
        _check_sd("sdxl", keys, modules),
    ]
    passed = [verdict for verdict in verdicts if verdict.ok]
    extra: tuple[str, ...] = ()
    if len(passed) == 1:
        verdict = passed[0]
        return FamilyDetection(
            "detected",
            verdict.family,
            (verdict.family,),
            f"Detected {_label(verdict.family)} from the file structure",
            tuple((verdict.evidence or []) + list(extra))[:_MAX_EVIDENCE],
        )
    if passed:
        names = tuple(verdict.family for verdict in passed)
        return FamilyDetection(
            "ambiguous",
            None,
            names,
            "Could be " + " or ".join(_label(name) for name in names) + "; choose one",
            extra,
        )
    notes = [verdict.note for verdict in verdicts if verdict.note]
    reason = notes[0] if notes else "The file structure does not clearly identify a supported family; choose one"
    return FamilyDetection("unknown", reason=reason, evidence=extra)


def _emit(path: Path, result: FamilyDetection, error_type: str | None = None) -> None:
    """One bounded diagnostic event: outcome, family, candidate count and a 3-character filename prefix only."""
    from dtflow.diagnostics import asset_ref_data, event as diagnostic_event

    data = {
        **asset_ref_data(filename=path.name),
        "outcome": result.outcome,
        "family": result.family,
        "candidates": len(result.candidates),
    }
    if error_type:
        data["error_type"] = error_type
    diagnostic_event(log, "lora_convert.source_family_detected", data=data)


def detect_source_family(path: Path) -> FamilyDetection:
    """Detect the family of one ``.safetensors`` LoRA from its header only; never raises.

    Reasons are fixed texts: no local path or exception detail reaches the Prepare row or the CLI report.
    """
    error_type: str | None = None
    try:
        with SafeTensors(path) as source:  # closed on exit: an open handle locks the file on Windows
            result = _detect(source)
    except OSError:
        result = FamilyDetection("unreadable", reason="Could not read the file.")
    except SourceReadError as error:
        reason = (
            "Could not read the file."
            if isinstance(error.__cause__, OSError)
            else "Not a readable safetensors file (invalid or truncated header)."
        )
        result = FamilyDetection("unreadable", reason=reason)
    except (KeyError, TypeError, ValueError, IndexError):
        result = FamilyDetection("unreadable", reason="Not a readable safetensors file (invalid tensor header).")
    except Exception as error:  # a detector bug must not fail the inspection of the other selected files
        error_type = type(error).__name__
        result = FamilyDetection("unknown", reason="Could not analyse this file; choose the family yourself.")
    _emit(path, result, error_type)
    return result
