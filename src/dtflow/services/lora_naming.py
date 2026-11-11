"""Deterministic, side-effect-free short-name suggestions and batch name allocation for LoRAs.

The filename parser is adapted from the standalone PR #377 proof of concept.
Its architecture vocabulary removes naming noise only: it never identifies
which model architecture a LoRA can run on.

This module does not read files or application state, contact DT+, or authorize
Prepare. Callers provide verified content identities and occupied names.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class NameSuggestion:
    """An editable, advisory short-name candidate extracted from a filename."""

    name: str | None
    alternatives: tuple[str, ...] = ()
    needs_review: bool = False
    confidence: str = "none"
    reason: str = ""


@dataclass(frozen=True, slots=True)
class NamingRequest:
    """One batch row with architecture/format evidence supplied independently."""

    short_name: str | None
    exact_architecture: str | None
    format_code: str | None
    family_tag: str | None = None
    manually_edited: bool = False
    alternatives: tuple[str, ...] = ()
    sha256: str | None = None
    size: int | None = None


@dataclass(frozen=True, slots=True)
class ManagedName:
    """An existing canonical name reserved for its measured content, if known."""

    short_name: str
    exact_architecture: str
    format_code: str
    sha256: str | None = None
    size: int | None = None


@dataclass(frozen=True, slots=True)
class NameAllocation:
    """Proposed batch result; not a persisted name or upload authorization."""

    short_name: str | None
    filename: str | None = None
    needs_review: bool = False
    conflict: str | None = None
    reuses_existing: bool = False

    @property
    def ready(self) -> bool:
        """Whether naming has everything needed for a managed-file preview."""
        return self.conflict is None and self.filename is not None


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
        # Prefer a concise descriptive phrase to simply accumulating more words.
        score = 2.7 * min(len(tokens), 2) + min(len(label), 16) * 0.12
        score -= 2.0 * max(0, len(tokens) - 2)
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


def _suggest(filename: str) -> dict[str, Any]:
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



def suggest_short_name(filename: str) -> NameSuggestion:
    """Suggest a name from text only, without file I/O or model inference."""
    result = _suggest(filename)
    return NameSuggestion(
        name=result["suggestion"],
        alternatives=tuple(result["alternatives"]),
        needs_review=result["needs_review"],
        confidence=result["confidence"],
        reason=result["reason"],
    )


_TAG = re.compile(r"^[A-Za-z0-9]+$")
_FORMATS = frozenset({"F16", "Q8P"})


def managed_filename(short_name: str, family_tag: str, format_code: str) -> str:
    """Build a managed technical filename from validated presentation metadata."""
    if not PORTABLE.fullmatch(short_name):
        raise ValueError("Short name must contain 1–20 ASCII letters, digits or underscores.")
    if not _TAG.fullmatch(family_tag):
        raise ValueError("Architecture shortcode must contain ASCII letters and digits.")
    if format_code not in _FORMATS:
        raise ValueError("LoRA format must be F16 or Q8P.")
    return f"{short_name}_{family_tag}_LoRA_{format_code}.ckpt"


def _identity(sha256: str | None, size: int | None) -> tuple[str, int] | None:
    """Only caller-supplied measured digests can establish content reuse."""
    return (sha256, size) if sha256 is not None and size is not None else None


def _scope(architecture: str, format_code: str, name: str) -> tuple[str, str, str]:
    return architecture.casefold(), format_code, name.casefold()


def _prefixes(name: str, alternatives: tuple[str, ...]) -> tuple[str, ...]:
    """Offer only complete candidate phrases; never trim arbitrary characters."""
    candidates = {name, *(alt for alt in alternatives if name.casefold().startswith(alt.casefold()))}
    return tuple(sorted((value for value in candidates if PORTABLE.fullmatch(value)), key=lambda v: (-len(v), v)))


def allocate_batch_names(
    requests: Sequence[NamingRequest],
    occupied: Collection[ManagedName] = (),
) -> tuple[NameAllocation, ...]:
    """Propose scoped unique names, preserving manual edits and existing identities.

    Requests are processed in supplied batch order. Only automatic suggestions
    may acquire numeric suffixes. The caller must repeat an authoritative
    canonical-name/content check inside its Prepare write transaction.
    """
    claimed: dict[tuple[str, str, str], ManagedName | NamingRequest] = {}
    known_content: dict[tuple[str, int], ManagedName] = {}
    for old in occupied:
        claimed.setdefault(_scope(old.exact_architecture, old.format_code, old.short_name), old)
        identity = _identity(old.sha256, old.size)
        if identity is not None:
            known_content.setdefault(identity, old)

    # A later user-edited row should not be displaced by an earlier suggestion.
    reserved_manual = {
        _scope(item.exact_architecture, item.format_code, item.short_name)
        for item in requests
        if item.manually_edited
        and item.short_name is not None
        and PORTABLE.fullmatch(item.short_name)
        and item.exact_architecture
        and item.format_code in _FORMATS
    }
    results: list[NameAllocation] = []
    for item in requests:
        name = item.short_name
        if not name:
            results.append(NameAllocation(None, conflict="missing_short_name"))
            continue
        if not PORTABLE.fullmatch(name):
            results.append(NameAllocation(name, conflict="invalid_short_name"))
            continue
        if not item.exact_architecture:
            results.append(NameAllocation(name, conflict="missing_architecture"))
            continue
        if item.format_code not in _FORMATS:
            results.append(NameAllocation(name, conflict="missing_format"))
            continue
        if item.family_tag is not None and not _TAG.fullmatch(item.family_tag):
            results.append(NameAllocation(name, conflict="invalid_family_tag"))
            continue

        identity = _identity(item.sha256, item.size)
        canonical = known_content.get(identity) if identity is not None else None
        if canonical is not None and _scope(
            canonical.exact_architecture, canonical.format_code, canonical.short_name
        ) != _scope(item.exact_architecture, item.format_code, name):
            # The canonical record owns an accepted name for these bytes. Neither
            # a heuristic nor a manual edit silently renames an existing asset.
            results.append(NameAllocation(name, conflict="content_already_named"))
            continue

        key = _scope(item.exact_architecture, item.format_code, name)
        existing = claimed.get(key)
        reuses = existing is not None and identity is not None and identity == _identity(
            existing.sha256, existing.size
        )
        collision = (existing is not None and not reuses) or (
            not item.manually_edited and key in reserved_manual and existing is None
        )
        if collision and item.manually_edited:
            results.append(NameAllocation(name, conflict="managed_name_collision"))
            continue

        renamed = False
        if collision:
            # The upper bound is sufficient for the finite set of already
            # occupied/claimed names, without an unbounded suffix search.
            limit = len(claimed) + len(reserved_manual) + len(requests) + 2
            replacement = None
            for number in range(2, limit):
                suffix = f"_{number}"
                for prefix in _prefixes(name, item.alternatives):
                    proposed = prefix + suffix
                    if len(proposed) > MAX_SHORT_NAME:
                        continue
                    proposed_key = _scope(item.exact_architecture, item.format_code, proposed)
                    if proposed_key not in claimed and proposed_key not in reserved_manual:
                        replacement = proposed
                        break
                if replacement is not None:
                    break
            if replacement is None:
                results.append(NameAllocation(name, conflict="no_available_whole_word_name"))
                continue
            name = replacement
            key = _scope(item.exact_architecture, item.format_code, name)
            renamed = True

        if not reuses:
            claimed[key] = item
        if identity is not None and canonical is None:
            known_content.setdefault(identity, ManagedName(name, item.exact_architecture, item.format_code, *identity))
        filename = (
            managed_filename(name, item.family_tag, item.format_code)
            if item.family_tag is not None
            else None
        )
        results.append(
            NameAllocation(
                short_name=name,
                filename=filename,
                needs_review=renamed,
                reuses_existing=reuses,
            )
        )
    return tuple(results)
