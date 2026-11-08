"""Managed output file layout: one folder per run, readable file names, and the legacy per-attempt layout.

Pure naming and parsing, no I/O. Every consumer that builds or validates a managed output path goes through
here, so the layout is defined once (issue #296, ``devdocs/architecture/output-layout.md``).

New layout (``outputs/<run folder>/``):
  ``<prefix>-<attempt id>-<n>.png``   final image n (1-based), gallery ordinal n - 1
  ``<prefix>-<attempt id>-raw-1.png`` provider evidence of an image operation
  ``<prefix>-<attempt id>.json``      the attempt's metadata sidecar

Legacy layout (kept readable and removable forever): ``outputs/<attempt id>/final-<n>.png``,
``raw-1.png`` and ``metadata.json``.
"""

import re
from dataclasses import dataclass
from datetime import datetime

_ATTEMPT = r"[0-9a-f]{32}"
_PREFIX = r"[a-z0-9][a-z0-9-]{0,62}"
FOLDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_LEGACY_FINAL = re.compile(r"final-([1-9][0-9]*)\.png\Z")
_FINAL = re.compile(rf"({_PREFIX})-({_ATTEMPT})-([1-9][0-9]*)\.png\Z")
_RAW = re.compile(rf"({_PREFIX})-({_ATTEMPT})-raw-1\.png\Z")
_SIDECAR = re.compile(rf"({_PREFIX})-({_ATTEMPT})\.json\Z")
_ATTEMPT_ONLY = re.compile(_ATTEMPT + r"\Z")
MAX_PREFIX = 48
MAX_SLUG = 24


@dataclass(frozen=True, slots=True)
class OutputLayout:
    """Where one attempt's files go: the run folder and the readable file-name prefix."""

    group: str
    label: str = "image"


def slug(text: object, limit: int = MAX_SLUG, default: str = "image") -> str:
    """Lower-case ASCII ``a-z0-9-`` of at most ``limit`` characters; never empty, never starting with ``-``."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:limit].strip("-")
    return cleaned or default


def group_name(kind: str, run_id: str, started_at: datetime | None = None, name: str | None = None) -> str:
    """``workflow-<name>-<run id>``, with a ``YYYYMMDD-HHMMSS-`` prefix when a start time is given.

    Runs that have a stable id (queue item, workflow run, script run) pass no time, so a restart or a later
    per-image Retry of the same run lands in the same folder. At most 80 characters."""
    parts = [started_at.strftime("%Y%m%d-%H%M%S")] if started_at else []
    parts.append(slug(kind, 12, "run"))
    if name:
        parts.append(slug(name, 24, ""))
    parts.append(slug(run_id, 8, "run"))
    return "-".join(part for part in parts if part)[:80].strip("-.")


def prompt_label(prompt: object) -> str:
    """A short readable label from the start of a prompt (``a-red-fox-in-snow``)."""
    return slug(prompt, MAX_SLUG)


def file_prefix(label: str, seed: object = None) -> str:
    """``<label>-s<seed>``; the seed is part of the name only when it is a plain integer."""
    prefix = slug(label, MAX_SLUG)
    if type(seed) is int and 0 <= seed < 10**12:
        prefix += f"-s{seed}"
    return prefix[:MAX_PREFIX].strip("-")


def final_name(prefix: str, attempt_id: str, ordinal: int) -> str:
    return f"{prefix}-{attempt_id}-{ordinal + 1}.png"


def raw_name(prefix: str, attempt_id: str) -> str:
    return f"{prefix}-{attempt_id}-raw-1.png"


def sidecar_name(prefix: str, attempt_id: str) -> str:
    return f"{prefix}-{attempt_id}.json"


def parse_final(name: str) -> tuple[str | None, int] | None:
    """``(attempt id or None for legacy, ordinal)`` of a managed final image file name, else None."""
    match = _FINAL.fullmatch(name)
    if match:
        return match[2], int(match[3]) - 1
    legacy = _LEGACY_FINAL.fullmatch(name)
    return (None, int(legacy[1]) - 1) if legacy else None


def companion_names(folder: str, name: str, attempt_id: str, ordinal: int) -> tuple[str, str | None] | None:
    """``(sidecar name, raw name or None)`` when ``folder/name`` is exactly this attempt's final ``ordinal``.

    Legacy: folder is the attempt id, the sidecar ``metadata.json`` and raw ``raw-1.png``. New: both are derived
    from the final's own prefix. None means the name is not this attempt's final at all, so nothing is ever
    authorized by a pattern that merely looks similar.
    """
    if not _ATTEMPT_ONLY.fullmatch(attempt_id) or ordinal < 0:
        return None
    legacy = _LEGACY_FINAL.fullmatch(name)
    if legacy:
        if folder != attempt_id or int(legacy[1]) - 1 != ordinal:
            return None
        return "metadata.json", "raw-1.png"
    match = _FINAL.fullmatch(name)
    if not match or match[2] != attempt_id or int(match[3]) - 1 != ordinal or not FOLDER.fullmatch(folder):
        return None
    return sidecar_name(match[1], attempt_id), raw_name(match[1], attempt_id)


def raw_of_first_final(name: str, attempt_id: str) -> str | None:
    """The provider-evidence file name that belongs to final image 1 named ``name``, else None."""
    legacy = _LEGACY_FINAL.fullmatch(name)
    if legacy:
        return "raw-1.png" if legacy[1] == "1" else None
    match = _FINAL.fullmatch(name)
    if match and match[2] == attempt_id and match[3] == "1":
        return raw_name(match[1], attempt_id)
    return None


def is_metadata_name(name: str) -> bool:
    """A managed sidecar: legacy ``metadata.json`` or a new ``<prefix>-<attempt id>.json``."""
    return name == "metadata.json" or _SIDECAR.fullmatch(name) is not None


def output_key(path: str) -> re.Match | None:
    """Match the trailing ``outputs/<folder>/<final file>`` of a recorded path (either separator, any case)."""
    normalized = path.replace("\\", "/")
    return re.search(r"(?:^|/)outputs/([A-Za-z0-9][A-Za-z0-9._-]{0,79})/([^/]+\.png)\Z", normalized, re.IGNORECASE)


def identity_from_path(path: object) -> str | None:
    """The gallery identity ``<attempt id>:<ordinal>`` a managed final's path names, else None.

    Legacy paths name it by folder and ordinal; new ones carry the attempt id in the file name. Inferred from the
    path only; every consumer re-checks it against the gallery record.
    """
    if not isinstance(path, str):
        return None
    normalized = path.replace("\\", "/")
    parts = normalized.rsplit("/", 2)
    if len(parts) < 2:
        return None
    parsed = parse_final(parts[-1])
    if parsed is None:
        return None
    attempt, ordinal = parsed
    if attempt is None:
        folder = parts[-2]
        return f"{folder}:{ordinal}" if _ATTEMPT_ONLY.fullmatch(folder) else None
    return f"{attempt}:{ordinal}"
