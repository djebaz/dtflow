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
    ("modelspec.title", "Title in file", 120),
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

    def retained_record(self) -> dict | None:
        """The record kept with a retained conversion: only what describes this LoRA's training (never the file's
        own title, author, license, date or description), including declared base-model information."""
        fields = {key: value for key, value in self.fields.items() if key in _RETAINED}
        if not fields and not self.tags:
            return None
        record = {
            "version": RECORD_VERSION,
            "fields": fields,
            "tags": [[tag, count] for tag, count in self.tags],
        }
        declared = declared_family({"fields": self.fields})
        if declared:
            record["declared_family"] = declared
        return record


NO_METADATA = SourceMetadata({})


def _clean(value: str, limit: int) -> str:
    # Normalize only a bounded prefix: an untrusted header can contain very large text fields.
    text = re.sub(r"\s+", " ", value[: limit * 4]).strip()
    return text[:limit].rstrip() if text.casefold() not in _EMPTY else ""


def _exact(value: str, limit: int) -> str | None:
    """The cleaned text, "" when empty, or None when it is too long to be what it claims to be (never truncated)."""
    if len(value) > limit:
        return None
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
                isinstance(count, int) or (math.isfinite(count) and count.is_integer())
            )  # JSON allows Infinity and NaN; frequency counts must be whole numbers
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
            value = re.split(r"[\\/]", value[-limit * 4 :].strip())[-1]  # last path component only
        if key == "modelspec.trigger_phrase" and _exact(value, limit) is None:
            continue  # a long phrase must not be retained as a misleading truncated trigger
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
    (re.compile(r"stable[-_ ]diffusion[-_ ]xl[-_ ]v?1(?:\.0)?[-_ ]base|sdxl"), "sdxl"),
    (re.compile(r"stable-diffusion-v1|\bsd[-_ ]?1"), "sd1"),
)


def declared_family(record: dict | None) -> str | None:
    """Converter family key the file *declares* (``modelspec.architecture``, else ``ss_base_model_version``).

    For display and an informational comparison only: never an input to detection, suggestion or conversion.
    """
    kept = (record or {}).get("declared_family")
    if isinstance(kept, str) and kept:  # older retained records may have only the derived family
        return kept
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


# Only what describes this LoRA's training is shown, including its declared architecture/base model.
# The file's own title, author, license, date and description stay out of the app; the CLI JSON report keeps them.
_FACT_ORDER = (
    "ss_output_name",
    "modelspec.architecture",
    "ss_base_model_version",
    "ss_sd_model_name",
    "modelspec.trigger_phrase",
    "ss_class_tokens",
    "ss_network_module",
    "ss_network_dim",
    "ss_network_alpha",
    "ss_resolution",
    "modelspec.resolution",
    "ss_num_epochs",
    "modelspec.usage_hint",
)
_RETAINED = frozenset(_FACT_ORDER)
_TAGS_SHOWN = 12


@dataclass(frozen=True, slots=True)
class DetailModel:
    """Presentation-neutral content of a details view (no Qt): a one-line summary, label/value facts, tags."""

    summary: str = ""
    note: str = ""
    facts: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()
    # The summary as (text, strong) runs: values are strong, labels and separators muted. Display only; the view
    # draws them as plain text, so file content is never interpreted as markup.
    runs: tuple[tuple[str, bool], ...] = ()
    tag_counts: tuple[tuple[str, int], ...] = ()

    @property
    def note_runs(self) -> tuple[tuple[str, bool], ...]:
        return ((" · " if self.runs else "", False), (self.note, False)) if self.note else ()

    @property
    def tag_runs(self) -> tuple[tuple[str, bool], ...]:
        return (("Top tags: ", False), (" · ".join(self.tags), True)) if self.tags else ()

    @property
    def line(self) -> str:
        """The one-line form (summary, then the informational mismatch note when there is one)."""
        return " · ".join(part for part in (self.summary, self.note) if part)

    @property
    def tooltip(self) -> str:
        """All selected bounded training facts and retained tags, not the shortened inline summary."""
        lines = [f"{label}: {value}" for label, value in self.facts]
        if not lines and self.summary:
            lines.append(self.summary)
        if self.note:
            lines.append(self.note)
        tags = " · ".join(f"{tag} ({count})" for tag, count in self.tag_counts)
        if not tags:
            tags = " · ".join(self.tags)
        if tags:
            lines.append("Top training tags (not necessarily trigger words): " + tags)
        return "\n".join(lines)

    @property
    def tags_line(self) -> str:
        return "Top tags: " + " · ".join(self.tags) if self.tags else ""

    def __bool__(self) -> bool:
        return bool(self.summary or self.facts or self.tags)


def detail_model(
    record: dict | None, *, detected_family: str | None = None, family_label=lambda key: key
) -> DetailModel:
    """Everything a details view shows, from a bounded record. Never raises on a malformed record."""
    fields = (record or {}).get("fields")
    fields = (
        {k: v for k, v in fields.items() if isinstance(k, str) and isinstance(v, str) and v}
        if isinstance(fields, dict)
        else {}
    )
    tags_raw = (record or {}).get("tags")
    retained_tags = (tags_raw if isinstance(tags_raw, list) else [])[:MAX_TAGS]
    tags = tuple(
        item[0] for item in retained_tags[:_TAGS_SHOWN] if isinstance(item, list) and item and isinstance(item[0], str)
    )
    tag_counts = tuple(
        (item[0], item[1])
        for item in retained_tags
        if isinstance(item, list)
        and len(item) == 2
        and isinstance(item[0], str)
        and isinstance(item[1], int)
        and not isinstance(item[1], bool)
        and item[1] >= 0
    )
    if not fields and not tags:
        return DetailModel()
    declared = declared_family(record)
    # Display one phrase, not a list of activations. Keep the source record and editable Trigger Words untouched.
    trigger = next(
        (part.strip() for part in re.split(r"[,;\n]", fields.get("modelspec.trigger_phrase", "")) if part.strip()), None
    )
    facts: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in _FACT_ORDER:
        value = trigger if key == "modelspec.trigger_phrase" else fields.get(key)
        label = (
            "Trained as"
            if key == "ss_output_name"
            else "ModelSpec resolution"
            if key == "modelspec.resolution"
            else _LABELS[key]
        )
        if not value or label in seen:
            continue
        facts.append((label, value))
        seen.add(label)
    rank, alpha = fields.get("ss_network_dim"), fields.get("ss_network_alpha")
    resolution = fields.get("ss_resolution") or fields.get("modelspec.resolution")
    epochs = fields.get("ss_num_epochs")
    base_model = fields.get("ss_sd_model_name") or fields.get("ss_base_model_version")
    parts: list[list[tuple[str, bool]]] = [
        [("Trained as ", False), (fields["ss_output_name"], True)] if fields.get("ss_output_name") else [],
        [("base ", False), (base_model, True)] if base_model else [],
        [("rank ", False), (rank, True), (" / alpha ", False), (alpha, True)]
        if rank and alpha
        else [("rank ", False), (rank, True)]
        if rank
        else [],
        [(resolution, True)] if resolution else [],
        [(epochs, True), (" epochs", False)] if epochs else [],
        [("trigger: ", False), (f"{trigger[:40]}{'…' if len(trigger) > 40 else ''}", True)] if trigger else [],
    ]
    runs: list[tuple[str, bool]] = []
    for part in (part for part in parts if part):
        runs += [(" · ", False)] if runs else []
        runs += part
    if not runs and facts:
        label, value = facts[0]
        runs = [(f"{label}: ", False), (value, True)]
    elif not runs and tags:
        runs = [("Training tags available", False)]
    summary = "".join(text for text, _strong in runs)
    note = ""
    if declared and detected_family and declared != detected_family:
        note = (
            f"Declared {family_label(declared)}, structure looks like {family_label(detected_family)} (informational)"
        )
    return DetailModel(summary, note, tuple(facts), tags, tuple(runs), tag_counts)
