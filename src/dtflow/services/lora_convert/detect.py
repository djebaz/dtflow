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

    return _ResolverRule("krea2", krea2.resolve_krea2, tag, lambda m: bool(qkv.match(m)), _KREA2_WIDTH)


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

    return _ResolverRule("zimage", zimage.resolve_zimage, tag, lambda m: bool(qkv_w13.match(m)), _ZIMAGE_WIDTH)


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

    return _ResolverRule("flux1", flux1.resolve_flux1, tag, lambda m: bool(qkv.match(m)), None)


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
    verdict.ok = True
    verdict.evidence = [
        f"all {len(modules)} modules map to {label} tables",
        f"cross-attention width {expected} ({sum(widths.values())} modules)",
    ]
    if family == "sdxl":
        verdict.evidence.append("second text encoder present" if has_te2 else "no second text encoder in the file")
    else:
        verdict.evidence.append("no SDXL text encoder namespace")
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

    verdicts = [
        _check_resolver(_krea2_rule(), modules),
        _check_resolver(_zimage_rule(), modules),
        _check_resolver(_flux1_rule(), modules),
        _check_sd("sd1", keys, modules),
        _check_sd("sdxl", keys, modules),
    ]
    passed = [verdict for verdict in verdicts if verdict.ok]
    extra = (f"{other} tensors are not plain LoRA up/down/alpha keys",) if other else ()
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


def _emit(result: FamilyDetection) -> None:
    """One bounded diagnostic event: outcome, family and candidate count; no paths, names or keys."""
    from dtflow.diagnostics import event as diagnostic_event

    diagnostic_event(
        log,
        "lora_convert.source_family_detected",
        data={"outcome": result.outcome, "family": result.family, "candidates": len(result.candidates)},
    )


def detect_source_family(path: Path) -> FamilyDetection:
    """Detect the family of one ``.safetensors`` LoRA from its header only; never raises for bad files."""
    try:
        with SafeTensors(path) as source:  # closed on exit: an open handle locks the file on Windows
            result = _detect(source)
    except (SourceReadError, OSError) as error:
        result = FamilyDetection("unreadable", reason=str(error))
    except (KeyError, TypeError, ValueError, IndexError) as error:
        result = FamilyDetection("unreadable", reason=f"{path.name}: invalid tensor header ({error}).")
    _emit(result)
    return result
