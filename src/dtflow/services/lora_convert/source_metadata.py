"""Descriptive metadata of a safetensors LoRA source (#414), read from the header only.

Everything here is display and suggestion data. It is never evidence for the source family (#402 stays structural),
never changes conversion, and a missing, malformed or contradictory value is simply left out. Values are file-declared
and may be stale after a merge or a re-save, so they are always presented as "declared by the file".
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

RECORD_VERSION = 1
MAX_TAGS = 50  # retained training tags
MAX_TAG_FREQUENCY_CHARS = 1024 * 1024  # a larger ss_tag_frequency string is not parsed at all
MAX_TRIGGER_CHARS = 200
_MAX_COUNT = 10**12

# (header key, label, maximum stored characters). Dataset folders and paths (ss_dataset_dirs, ss_datasets) and free
# training comments are deliberately not kept: they can hold local paths or personal text.
_FIELDS: tuple[tuple[str, str, int], ...] = (
    ("modelspec.title", "Title", 120),
    ("ss_output_name", "Output name", 120),
    ("modelspec.author", "Author", 120),
    ("modelspec.description", "Description", 400),
    ("modelspec.date", "Date", 40),
    ("modelspec.license", "License", 120),
    ("modelspec.architecture", "Declared architecture", 80),
    ("ss_base_model_version", "Declared base model", 80),
    ("ss_sd_model_name", "Base model file", 120),
    ("ss_network_module", "Network type", 120),
    ("ss_network_dim", "Rank", 16),
    ("ss_network_alpha", "Alpha", 16),
    ("ss_resolution", "Resolution", 40),
    ("modelspec.resolution", "Resolution", 40),
    ("ss_num_epochs", "Epochs", 16),
    ("ss_class_tokens", "Class tokens", 120),
    ("modelspec.trigger_phrase", "Trigger phrase", MAX_TRIGGER_CHARS),
    ("modelspec.usage_hint", "Usage hint", 300),
)
_LABELS = {key: label for key, label, _limit in _FIELDS}
_EMPTY = {"", "none", "null", "n/a"}


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Bounded descriptive metadata of one source file, plus the explicit trigger suggestion if there is one."""

    fields: dict[str, str]
    tags: tuple[tuple[str, int], ...] = ()
    trigger: str | None = None
    trigger_source: str | None = None

    def record(self) -> dict | None:
        """JSON-ready bounded record kept with the conversion provenance, or None when nothing useful was found."""
        if not self.fields and not self.tags:
            return None
        return {
            "version": RECORD_VERSION,
            "fields": dict(self.fields),
            "tags": [[tag, count] for tag, count in self.tags],
        }


NO_METADATA = SourceMetadata({})


def _clean(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text[:limit].rstrip() if text.casefold() not in _EMPTY else ""


def _exact(value: str, limit: int) -> str | None:
    """The cleaned text, "" when empty, or None when it is too long to be what it claims to be (never truncated)."""
    text = re.sub(r"\s+", " ", value).strip()
    if text.casefold() in _EMPTY:
        return ""
    return text if len(text) <= limit else None


def _tag_frequency(raw: str | None) -> dict[str, dict[str, int]] | None:
    """``ss_tag_frequency``: a JSON string of dataset -> tag -> count; anything else is ignored."""
    if not raw or len(raw) > MAX_TAG_FREQUENCY_CHARS:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, RecursionError):  # includes deeply nested input
        return None
    if not isinstance(parsed, dict):
        return None
    datasets: dict[str, dict[str, int]] = {}
    for dataset, tags in parsed.items():
        if not isinstance(dataset, str) or not isinstance(tags, dict):
            continue
        counts = {
            tag: min(int(count), _MAX_COUNT)  # a huge integer (hundreds of digits) is clamped, never an error
            for tag, count in tags.items()
            if isinstance(tag, str)
            and isinstance(count, int | float)
            and not isinstance(count, bool)
            and (
                isinstance(count, int) or math.isfinite(count)
            )  # JSON allows Infinity and NaN; isfinite of a huge int overflows
            and count > 0
        }
        if counts:
            datasets[dataset] = counts
    return datasets or None


def _top_tags(datasets: dict[str, dict[str, int]]) -> tuple[tuple[str, int], ...]:
    total: dict[str, int] = {}
    for counts in datasets.values():
        for tag, count in counts.items():
            clean = _clean(tag, 40)
            if clean:
                total[clean] = total.get(clean, 0) + count
    ranked = sorted(total.items(), key=lambda item: (-item[1], item[0].casefold()))
    return tuple(ranked[:MAX_TAGS])


def _single_tag_trigger(datasets: dict[str, dict[str, int]] | None) -> str | None:
    """ai-toolkit writes its configured trigger word as a one-tag dataset ``{"1_<word>": {"<word>": 1}}``."""
    if not datasets or len(datasets) != 1:
        return None
    ((dataset, counts),) = datasets.items()
    if len(counts) != 1:
        return None
    ((tag, count),) = counts.items()
    word = _exact(tag, MAX_TRIGGER_CHARS)
    return word if word and count == 1 and dataset == f"1_{tag}" else None


def extract(raw: dict[str, str]) -> SourceMetadata:
    """Select the bounded, useful fields from a raw header map. Never raises."""
    fields: dict[str, str] = {}
    for key, _label, limit in _FIELDS:
        value = raw.get(key)
        if not isinstance(value, str):
            continue
        if key == "ss_sd_model_name":
            value = re.split(r"[\\/]", value.strip())[-1]  # a file name at training time, never a folder
        text = _clean(value, limit)
        if text:
            fields[key] = text
    datasets = _tag_frequency(raw.get("ss_tag_frequency"))
    phrase = raw.get("modelspec.trigger_phrase")
    trigger = _exact(phrase, MAX_TRIGGER_CHARS) if isinstance(phrase, str) else ""  # too long: None, never truncated
    source = "modelspec.trigger_phrase" if trigger else None
    if trigger == "":
        # Only an explicit single-tag declaration counts; the general tag vocabulary is never a trigger.
        trigger = _single_tag_trigger(datasets)
        source = "single-tag ss_tag_frequency" if trigger else None
    return SourceMetadata(fields, _top_tags(datasets) if datasets else (), trigger or None, source if trigger else None)


def extract_safely(raw: dict[str, str]) -> SourceMetadata:
    """Best effort: whatever goes wrong while reading descriptive metadata, the file simply has none."""
    try:
        return extract(raw)
    except Exception:  # noqa: BLE001 - descriptive data must never fail Prepare or discard a conversion
        return NO_METADATA


# Exact families only: a Flux 2 or Krea 1 label must not be shown as Flux 1 or Krea 2, so those stay unmapped.
_DECLARED_FAMILIES = (
    (re.compile(r"krea[-_ ]?2"), "krea2"),
    (re.compile(r"z[-_ ]?image"), "zimage"),
    (re.compile(r"flux(?![-_. ]?(?:2|klein))"), "flux1"),
    (re.compile(r"stable-diffusion-xl-v1-base|sdxl"), "sdxl"),
    (re.compile(r"stable-diffusion-v1|\bsd[-_ ]?1"), "sd1"),
)


def declared_family(record: dict | None) -> str | None:
    """Converter family key the file *declares* (``modelspec.architecture``, else ``ss_base_model_version``).

    For display and an informational comparison only: never an input to detection, suggestion or conversion.
    """
    fields = (record or {}).get("fields")
    if not isinstance(fields, dict):
        return None
    for key in ("modelspec.architecture", "ss_base_model_version"):
        value = fields.get(key)
        if isinstance(value, str):
            lowered = value.casefold()
            if "refiner" in lowered:
                return None
            for pattern, family in _DECLARED_FAMILIES:
                if pattern.search(lowered):
                    return family
    return None


def detail_lines(record: dict | None, *, detected_family: str | None = None, family_label=lambda key: key) -> list[str]:
    """Read-only, scannable lines for a details panel. Essential fields first; long text is already capped."""
    fields = (record or {}).get("fields")
    if not isinstance(fields, dict) or not fields:
        return _tag_line(record)
    lines: list[str] = []
    shown: set[str] = set()
    for key, label, _limit in _FIELDS:
        value = fields.get(key)
        if isinstance(value, str) and value and label not in shown:
            lines.append(f"{label}: {value}")
            shown.add(label)
    declared = declared_family(record)
    if declared and detected_family and declared != detected_family:
        lines.append(
            f"Note: the file declares {family_label(declared)}, but its structure looks like "
            f"{family_label(detected_family)}. Declared metadata is informational only."
        )
    lines.extend(_tag_line(record))
    return lines


def _tag_line(record: dict | None) -> list[str]:
    tags = (record or {}).get("tags")
    if not isinstance(tags, list) or not tags:
        return []
    names = [item[0] for item in tags[:12] if isinstance(item, list) and item and isinstance(item[0], str)]
    return ["Top training tags (not necessarily trigger words): " + ", ".join(names)] if names else []
