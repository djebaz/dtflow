#!/usr/bin/env python3
"""Explore short LoRA labels from source filenames without modifying DTFlow."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

MAX_SHORT_NAME = 20

# These tokens describe packaging, training or architecture, not the LoRA's subject.
PACKAGING = {
    "lora",
    "ckpt",
    "safetensors",
    "version",
    "f16",
    "fp16",
    "bf16",
    "f32",
    "q8p",
    "q6p",
    "q5p",
    "q4p",
    "i8x",
    "ti",
    "epoch",
    "step",
    "final",
    "rank",
    "weighted",
    "adamw",
    "adamw8bit",
    "s4000",
    "s3400",
    "r16",
    "r32",
    "r64",
    "colab",
    "ios",
    "512",
    "1024",
    "2048",
}
# Complete architecture and shortcode vocabulary follows issue #373.
# The tokens below are filename-cleaning hints only, never family evidence.
ARCHITECTURE_MARKERS = {
    "C25x29B",
    "C25x2B",
    "cosmos",
    "cosmos2_5_2_9b",
    "cosmos2_5_2b",
    "cosmos2.5_2.9b",
    "cosmos2.5_2b",
    "cosmos25",
    "Erni",
    "ernie",
    "ernie_image",
    "ernieimage",
    "ernieImage",
    "F1",
    "F2",
    "F2x4B",
    "F2x9B",
    "F3x5B",
    "flux",
    "flux1",
    "flux2",
    "flux2_4b",
    "flux2_9b",
    "flux3",
    "flux3_5b",
    "fluxklein",
    "HDI1",
    "hidream",
    "hidream_i1",
    "hiDreamI1",
    "hunyuan",
    "hunyuan_video",
    "hunyuanVideo",
    "HYV",
    "ID4",
    "ideogram",
    "ideogram_4",
    "ideogram4",
    "illus",
    "Kr2",
    "krea",
    "krea_2",
    "krea2",
    "ltx",
    "ltx2",
    "ltx2_3",
    "ltx2.3",
    "ltx23",
    "miniH3",
    "minimax",
    "minimax_h3",
    "minimaxh3",
    "minimaxH3",
    "moss",
    "Moss",
    "moss_image",
    "mossimage",
    "mossImage",
    "Pix",
    "pixart",
    "qwen",
    "qwen_image",
    "qwen_image_2.1",
    "qwenimage",
    "qwenImage",
    "qwenImage2_1",
    "qwenimage21",
    "Qwn",
    "Qwn21",
    "schnell",
    "sd1",
    "SD1",
    "sd2",
    "SD2",
    "sd3",
    "SD3",
    "sd3_large",
    "SD3L",
    "sd3Large",
    "sdxl",
    "sdxl_base",
    "sdxl_base_v0.9",
    "sdxl_refiner",
    "sdxl_refiner_v0.9",
    "sdxlBase",
    "sdxlRefiner",
    "SSD",
    "ssd_1b",
    "ssd1b",
    "W21x13B",
    "W21x14B",
    "W22x5B",
    "wan",
    "wan_v2.1_1.3b",
    "wan_v2.1_14b",
    "wan_v2.2_5b",
    "wan21",
    "wan21_1_3b",
    "wan21_14b",
    "wan22",
    "wan22_5b",
    "XL",
    "XLRef",
    "z_image",
    "zbase",
    "zi",
    "zimage",
    "zImage",
    "zimg",
    "ZiT",
}
_ARCHITECTURE_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9])(?:"
    + "|".join(
        r"[_\s.-]*".join(re.escape(part) for part in re.split(r"[_\s.-]+", marker))
        for marker in sorted(ARCHITECTURE_MARKERS, key=len, reverse=True)
    )
    + r")(?![a-z0-9])"
)
GENERIC = {"model", "checkpoint", "base", "new", "copy", "converted", "trained"}
WEAK = {"test", "draft", "colab", "ios", "foo", "small", "large", "final"}
ACRONYMS = {"nsfw": "NSFW", "xl": "XL", "gf": "GF", "hm": "HM", "4k": "4K"}
FORMAT_SUFFIX = re.compile(r"(?i)(?:[_\s.-]+(?:lora[_\s.-]*)?(?:f16|fp16|bf16|q8p|q6p|q5p|q4p))+$")
VERSION = re.compile(r"(?i)^v\d+(?:\.\d+)*$")
TRAINING = re.compile(r"(?i)^(?:s\d{3,}|step\d+|epoch\d+|r\d+|rank\d+|\d{5,}|adamw\w*)$")
PORTABLE = re.compile(r"^[A-Za-z0-9_]{1,20}$")


def _basename(filename: str) -> str:
    return filename.replace("\\", "/").rsplit("/", 1)[-1]


def _split(filename: str) -> tuple[list[list[str]], str | None, int]:
    stem = Path(_basename(filename)).stem
    stem = FORMAT_SUFFIX.sub("", stem)
    stem = _ARCHITECTURE_PATTERN.sub("", stem)
    stem = re.sub(r"(?i)(?<![a-z0-9])v(\d+)\.\d+(?=[_\s.-]|$)", r"v\1", stem)
    stem = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", stem)
    # A double underscore in converted names often separates alternate descriptions.
    raw_groups = re.split(r"_{2,}", stem)
    groups: list[list[str]] = []
    version = None
    discarded = 0
    for raw in raw_groups:
        tokens: list[str] = []
        for word in re.split(r"[_\s.\-]+", raw):
            if not word:
                continue
            lower = word.casefold()
            if VERSION.fullmatch(lower):
                version = "V" + lower[1:].split(".")[0]
                continue
            if lower in PACKAGING or TRAINING.fullmatch(lower):
                discarded += 1
                continue
            if lower in GENERIC:
                discarded += 1
                continue
            if word.isdigit() and len(word) > 4 and not 1900 <= int(word) <= 2100:
                discarded += 1
                continue
            if tokens and tokens[-1].casefold() == lower:
                discarded += 1
                continue
            tokens.append(word)
        if tokens:
            groups.append(tokens)
    return groups, version, discarded


def _format_token(token: str) -> str:
    if token.casefold() in ACRONYMS:
        return ACRONYMS[token.casefold()]
    if token.isdigit():
        return token
    return token[:1].upper() + token[1:].lower()


def _label(tokens: list[str], version: str | None) -> str:
    output = "".join(_format_token(token) for token in tokens)
    if version and not output.casefold().endswith(version.casefold()):
        output += version
    return output


def _candidates(groups: list[list[str]], version: str | None) -> list[tuple[str, float]]:
    # Keep the first descriptive word as the anchor. A word that is itself
    # too long is skipped intact; never truncate it or favor trailing numbers.
    words: list[str] = []
    group_ends: set[int] = set()
    for group in groups:
        for word in group:
            if len(_format_token(word)) <= MAX_SHORT_NAME:
                words.append(word)
        if words:
            group_ends.add(len(words))
    while words and not any(character.isalpha() for character in words[0]):
        words.pop(0)
        group_ends = {end - 1 for end in group_ends if end > 1}
    if not words:
        return []

    found: dict[str, tuple[str, float]] = {}
    for end in range(1, len(words) + 1):
        tokens = words[:end]
        label = _label(tokens, version)
        if len(label) > MAX_SHORT_NAME:
            # Optional version tags are dropped before any descriptive word.
            label = _label(tokens, None)
        if not PORTABLE.fullmatch(label):
            break
        score = 2.7 * len(tokens) + min(len(label), 16) * 0.12
        score -= max(0, len(label) - 16) * 0.3
        if end in group_ends:
            score += 2.0
        if version and label.casefold().endswith(version.casefold()):
            score += 1.2
        if len(tokens) == 1 and tokens[0].casefold() in WEAK:
            score -= 5
        if len(tokens) == 1 and len(tokens[0]) < 4:
            score -= 3
        found[label.casefold()] = (label, score)

    return sorted(found.values(), key=lambda item: (-item[1], item[0]))


def suggest(filename: str) -> dict[str, Any]:
    groups, version, discarded = _split(filename)
    candidates = _candidates(groups, version)
    words = [word for group in groups for word in group]
    if not candidates:
        return {
            "source_file": _basename(filename),
            "suggestion": None,
            "alternatives": [],
            "confidence": "none",
            "needs_review": True,
            "reason": "No usable descriptive tokens remain after removing technical naming noise.",
        }
    primary = candidates[0][0]
    alternatives = [name for name, _ in candidates[1:] if name != primary][:3]
    low_signal = (
        not words
        or all(token.casefold() in WEAK for token in words)
        or (len(words) == 1 and bool(re.fullmatch(r"[a-z]{1,4}\d+", words[0], re.I)))
    )
    shortened = len(_label(words, version)) > MAX_SHORT_NAME
    confidence = "low" if low_signal else "medium" if shortened or len(words) == 1 else "high"
    return {
        "source_file": _basename(filename),
        "suggestion": None if low_signal else primary,
        "alternatives": ([primary] + alternatives)[:3] if low_signal else alternatives,
        "confidence": confidence,
        "needs_review": low_signal or shortened,
        "reason": (
            "Not enough descriptive information; inspect alternatives or name manually."
            if low_signal
            else "Long filename compressed at word boundaries; verify meaning."
            if shortened
            else f"Removed {discarded} technical token(s); filename-derived guess only."
        ),
    }


def _is_probably_lora(entry: dict[str, Any]) -> bool:
    filename = str(entry.get("file", "")).casefold()
    if "lora" in filename:
        return True
    if entry.get("module_groups") and int(entry.get("tensor_count") or 0) > 0:
        return True
    if entry.get("format") == "safetensors" and int(entry.get("tensor_count") or 0) > 0:
        return True
    return False


def _model_family(entry: dict[str, Any]) -> str | None:
    """Use reported architecture evidence, never infer compatibility from a filename."""
    family = entry.get("detected_family")
    if not isinstance(family, str) or not family.strip():
        metadata = entry.get("metadata")
        family = metadata.get("ss_base_model_version") if isinstance(metadata, dict) else None
    if not isinstance(family, str) or not family.strip():
        return None
    aliases = {"krea2": "krea_2"}
    normalized = family.strip().casefold()
    return aliases.get(normalized, normalized)


def _quantization(entry: dict[str, Any]) -> tuple[str | None, str]:
    """Identify the LoRA format from the suffix, or use the converter's CKPT default."""
    filename = _basename(str(entry.get("file", "")))
    stem = Path(filename).stem
    matches = re.findall(r"(?i)(?:^|[_\s.-])(?:lora[_\s.-]+)?(f16|fp16|q8p)(?=$|[_\s.-])", stem)
    if matches:
        code = matches[-1].upper()
        return ("F16" if code == "FP16" else code), "filename_hint"
    if filename.casefold().endswith(".ckpt"):
        return "F16", "converter_default"
    return None, "unknown"


def analyze(entries: list[dict[str, Any]], *, include_other: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in entries:
        filename = entry.get("file")
        if not isinstance(filename, str) or not filename:
            continue
        if not include_other and not entry.get("_manual_name") and not _is_probably_lora(entry):
            continue
        result = suggest(filename)
        family = _model_family(entry)
        quantization, source = _quantization(entry)
        result["detected_family_evidence"] = entry.get("detected_family")
        result["model_family"] = family
        result["quantization"] = quantization
        result["quantization_source"] = source
        # A missing architecture/format cannot establish collision-free managed names.
        if family is None or quantization is None:
            result["needs_review"] = True
            result["collision_scope_incomplete"] = True
        # Structural classification is context, never a suggestion of compatibility.
        result["likely_lora"] = _is_probably_lora(entry)
        rows.append(result)

    # A managed filename includes architecture and format, so only identical
    # (short name, resolved family, quantization) tuples compete for suffixes.
    # This still only proposes names: Prepare requires user approval and checks
    # exact managed-file/content collisions before any upload.
    def scoped_key(row: dict[str, Any], name: str) -> tuple[str | None, str | None, str]:
        return (row["model_family"], row["quantization"], name.casefold())

    reserved = {scoped_key(row, row["suggestion"]) for row in rows if row["suggestion"] is not None}
    assigned: set[tuple[str | None, str | None, str]] = set()
    for row in rows:
        base = row["suggestion"]
        if base is None:
            continue
        base_key = scoped_key(row, base)
        if base_key not in assigned:
            assigned.add(base_key)
            continue

        groups, version, _ = _split(row["source_file"])
        # Preserve the first whole word; never truncate a word for "_2".
        prefixes = [base] + [name for name, _ in _candidates(groups, version) if base.startswith(name) and name != base]
        prefixes = sorted(set(prefixes), key=len, reverse=True)
        resolved = False
        for number in range(2, len(rows) + 2):
            suffix = f"_{number}"
            proposed = next(
                (name + suffix for name in prefixes if len(name) + len(suffix) <= MAX_SHORT_NAME),
                None,
            )
            if proposed is None:
                break
            key = scoped_key(row, proposed)
            if key in assigned or key in reserved:
                continue
            row["suggestion"] = proposed
            row["collision_suffix"] = number
            row["needs_review"] = True
            row["reason"] = "Same short name, model family and quantization; review the numbered suggestion."
            assigned.add(key)
            resolved = True
            break
        if not resolved:
            row["suggestion"] = None
            row["needs_review"] = True
            row["reason"] = "No complete-word candidate leaves room for a scoped numeric suffix."
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Explore optional LoRA short-name suggestions (no file writes).")
    parser.add_argument("--catalog", type=Path, help="JSON report containing a list of records with a file field")
    parser.add_argument("--name", action="append", default=[], help="Additional source filename (repeatable)")
    parser.add_argument("--include-other", action="store_true", help="Include likely non-LoRA entries")
    parser.add_argument("--json", action="store_true", help="Print structured JSON instead of the compact table")
    args = parser.parse_args()
    if args.catalog is None and not args.name:
        parser.error("pass --catalog and/or at least one --name")
    entries: list[dict[str, Any]] = []
    if args.catalog is not None:
        with args.catalog.open(encoding="utf-8") as stream:
            loaded = json.load(stream)
        if not isinstance(loaded, list):
            parser.error("catalog must contain a JSON list of records")
        entries.extend(entry for entry in loaded if isinstance(entry, dict))
    entries.extend({"file": name, "_manual_name": True} for name in args.name)
    rows = analyze(entries, include_other=args.include_other)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=True))
        return
    print(f"{'Suggestion':20}  {'Confidence':10} {'Review':6} Source")
    print("-" * 100)
    for row in rows:
        name = row["suggestion"] or "(no suggestion)"
        mark = "yes" if row["needs_review"] else ""
        print(f"{name:20}  {row['confidence']:10} {mark:6} {row['source_file']}")


if __name__ == "__main__":
    main()
