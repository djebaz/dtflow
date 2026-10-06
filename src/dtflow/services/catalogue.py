"""Bundled/user catalogue metadata resolved against one provider inventory.

The bundled catalogue classifies artifacts DTFlow knows about. User custom LoRA metadata is durable user data under the
state directory. Neither source establishes provider availability: selectors are always resolved against an
``InventorySnapshot``. Unknown inventory checkpoints remain explicitly discoverable so the editable raw model field can
use them without DTFlow inventing compatibility metadata.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from dtflow.core.models import InventorySnapshot

log = logging.getLogger(__name__)

CATALOGUE_DIR = Path(__file__).parents[1] / "resources" / "catalogue"
USER_CATALOGUE_DIR = "catalogue"
USER_LORAS_FILE = "custom_loras.json"
IMAGE_MODES = frozenset(("t2i", "i2i"))
VIDEO_MODES = frozenset(("t2v", "i2v"))
DOMAINS = ("all", "image", "video")

FAMILY_LABELS = {
    "sd_1": "SD 1.x",
    "sd_2": "SD 2.x",
    "sdxl": "SDXL",
    "sd3": "SD3",
    "flux_1": "FLUX.1",
    "flux_2": "FLUX.2",
    "z_image": "Z-Image",
    "qwen_image": "Qwen Image",
    "wan_2_1": "WAN 2.1",
    "wan_2_2": "WAN 2.2",
    "ltx_2": "LTX-2",
    "pixart": "PixArt",
    "auraflow": "AuraFlow",
    "kandinsky": "Kandinsky",
    "wurstchen": "Würstchen",
    "hidream_i1": "HiDream-I1",
}
FAMILY_ORDER = tuple(FAMILY_LABELS)


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    identifier: str
    kind: str
    base_model: str | None
    modes: frozenset[str]
    tier: str | None = None
    source: str = "bundled"
    name: str | None = None
    prefix: str | None = None

    @property
    def domains(self) -> frozenset[str]:
        result = set()
        if self.modes & IMAGE_MODES:
            result.add("image")
        if self.modes & VIDEO_MODES:
            result.add("video")
        return frozenset(result)

    def supports_domain(self, domain: str) -> bool:
        return domain == "all" or domain in self.domains


@dataclass(frozen=True, slots=True)
class CatalogueView:
    checkpoints: tuple[CatalogueEntry, ...]
    loras: tuple[CatalogueEntry, ...]
    unclassified_checkpoints: tuple[str, ...]


def _records(path: Path) -> list[dict]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning("catalogue ignored file=%s reason=%s", path.name, type(exc).__name__)
        return []
    if not isinstance(document, list):
        log.warning("catalogue ignored file=%s reason=shape", path.name)
        return []
    return [item for item in document if isinstance(item, dict)]


def _entry(record: dict, *, kind: str, tier: str | None = None, source: str = "bundled") -> CatalogueEntry | None:
    identifier = record.get("file")
    base_model = record.get("base_model")
    modes = record.get("modes", ())
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    if base_model is not None and not isinstance(base_model, str):
        base_model = None
    if not isinstance(modes, list) or any(not isinstance(mode, str) for mode in modes):
        modes = []
    name = record.get("name")
    prefix = record.get("prefix")
    return CatalogueEntry(
        identifier.strip(),
        kind,
        base_model if base_model and base_model != "unknown" else None,
        frozenset(modes),
        tier=tier,
        source=source,
        name=name if isinstance(name, str) and name.strip() else None,
        prefix=prefix if isinstance(prefix, str) else None,
    )


def validate_user_loras_document(document: object) -> list[dict]:
    """Validate imported user LoRA metadata without inferring missing compatibility."""
    if not isinstance(document, list):
        raise ValueError("Custom LoRA JSON must contain a list of LoRA definitions.")
    validated: list[dict] = []
    seen: set[str] = set()
    allowed_modes = IMAGE_MODES | VIDEO_MODES
    for index, record in enumerate(document, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"Custom LoRA entry {index} must be an object.")
        identifier = record.get("file")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"Custom LoRA entry {index} requires a non-empty 'file'.")
        identifier = identifier.strip()
        if identifier in seen:
            raise ValueError(f"Custom LoRA file is duplicated: {identifier}")
        seen.add(identifier)
        base_model = record.get("base_model")
        if base_model is not None and (not isinstance(base_model, str) or not base_model.strip()):
            raise ValueError(f"Custom LoRA entry {index} has an invalid 'base_model'.")
        modes = record.get("modes", [])
        if not isinstance(modes, list) or any(not isinstance(mode, str) for mode in modes):
            raise ValueError(f"Custom LoRA entry {index} has an invalid 'modes' list.")
        unknown_modes = set(modes) - allowed_modes
        if unknown_modes:
            raise ValueError(
                f"Custom LoRA entry {index} has unsupported mode(s): {', '.join(sorted(unknown_modes))}."
            )
        for key in ("name", "prefix"):
            value = record.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"Custom LoRA entry {index} has an invalid '{key}'.")
        validated.append(dict(record, file=identifier))
    return validated


def import_user_loras(source: Path, state_dir: Path) -> int:
    """Validate and atomically replace the durable user-owned custom LoRA catalogue."""
    try:
        document = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Custom LoRA JSON could not be read: {type(exc).__name__}.") from exc
    validated = validate_user_loras_document(document)
    target = Path(state_dir) / USER_CATALOGUE_DIR / USER_LORAS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(validated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(target)
    return len(validated)


class Catalogue:
    """Static classification plus one active provider's availability."""

    def __init__(self, state_dir: Path, *, bundled_dir: Path = CATALOGUE_DIR):
        self.state_dir = Path(state_dir)
        self.bundled_dir = Path(bundled_dir)
        self._prime = self._load("Models_prime.json", kind="checkpoint", tier="prime")
        self._niche = self._load("Models_niche.json", kind="checkpoint", tier="niche")
        self._bundled_loras = self._load("Loras.json", kind="lora")
        self._controls = self._load("Control.json", kind="control")
        self._inventory: InventorySnapshot | None = None

    def _load(
        self, filename: str, *, kind: str, tier: str | None = None, source: str = "bundled"
    ) -> tuple[CatalogueEntry, ...]:
        entries = (
            _entry(record, kind=kind, tier=tier, source=source)
            for record in _records(self.bundled_dir / filename)
        )
        return tuple(entry for entry in entries if entry is not None)

    def user_loras(self) -> tuple[CatalogueEntry, ...]:
        path = self.state_dir / USER_CATALOGUE_DIR / USER_LORAS_FILE
        entries = (
            _entry(record, kind="lora", source="user")
            for record in _records(path)
        )
        return tuple(entry for entry in entries if entry is not None)

    @property
    def user_loras_path(self) -> Path:
        return self.state_dir / USER_CATALOGUE_DIR / USER_LORAS_FILE

    def set_inventory(self, snapshot: InventorySnapshot | None) -> None:
        self._inventory = snapshot

    def _available_ids(self) -> set[str]:
        if self._inventory is None:
            return set()
        return {entry.identifier for entry in self._inventory.entries}

    def view(self) -> CatalogueView:
        available = self._available_ids()
        checkpoints_by_id: dict[str, CatalogueEntry] = {}
        for entry in (*self._niche, *self._prime):  # Prime wins an accidental exact-ID overlap.
            if entry.identifier in available:
                checkpoints_by_id[entry.identifier] = entry
        checkpoints = tuple(checkpoints_by_id.values())
        loras_by_id: dict[str, CatalogueEntry] = {}
        for entry in (*self._bundled_loras, *self.user_loras()):  # user metadata may refine a bundled definition.
            if entry.identifier in available:
                loras_by_id[entry.identifier] = entry
        loras = tuple(loras_by_id.values())
        known_entries = (*self._prime, *self._niche)
        known = {entry.identifier for entry in known_entries}
        known_nonmodels = {
            entry.identifier for entry in (*self._bundled_loras, *self.user_loras(), *self._controls)
        }
        unknown = ()
        if self._inventory is not None:
            metadata = dict(self._inventory.diagnostics).get("model_metadata") == "present"
            raw_unknown = {
                entry.identifier
                for entry in self._inventory.entries
                if entry.identifier not in known
                and entry.identifier not in known_nonmodels
                and (entry.kind == "model" or (entry.kind == "unknown" and not metadata))
            }
            classified_unknown = {
                entry.identifier
                for entry in known_entries
                if entry.identifier in available and (entry.base_model is None or not entry.domains)
            }
            unknown = tuple(sorted(raw_unknown | classified_unknown))
        return CatalogueView(checkpoints, loras, unknown)

    def checkpoints(
        self,
        *,
        domain: str = "all",
        base_model: str | None = None,
        include_niche: bool = False,
        include_unavailable: bool = False,
    ) -> tuple[CatalogueEntry, ...]:
        if domain not in DOMAINS:
            raise ValueError("Catalogue domain must be all, image or video.")
        source = (*self._prime, *self._niche) if include_unavailable else self.view().checkpoints
        entries = (
            entry
            for entry in source
            if (entry.tier == "prime" or include_niche)
            and entry.supports_domain(domain)
            and (base_model is None or entry.base_model == base_model)
        )
        return tuple(sorted(entries, key=lambda item: (item.tier == "niche", item.identifier.casefold())))

    def families(
        self, *, domain: str = "all", include_niche: bool = False, include_unavailable: bool = False
    ) -> tuple[tuple[str, str], ...]:
        present = {
            entry.base_model
            for entry in self.checkpoints(
                domain=domain, include_niche=include_niche, include_unavailable=include_unavailable
            )
            if entry.base_model is not None
        }
        ordered = [family for family in FAMILY_ORDER if family in present]
        ordered.extend(sorted(present - set(ordered)))
        return tuple((family, FAMILY_LABELS.get(family, family)) for family in ordered)

    def checkpoint(self, identifier: str, *, include_unavailable: bool = False) -> CatalogueEntry | None:
        entries = (*self._prime, *self._niche) if include_unavailable else self.view().checkpoints
        return next((entry for entry in entries if entry.identifier == identifier), None)

    def controls(self, *, domain: str = "all", base_model: str | None = None) -> tuple[CatalogueEntry, ...]:
        """Known available conditioning artifacts; no Control-specific UI is introduced by this change."""
        if domain not in DOMAINS:
            raise ValueError("Catalogue domain must be all, image or video.")
        available = self._available_ids()
        entries = (
            entry
            for entry in self._controls
            if entry.identifier in available
            and entry.supports_domain(domain)
            and (base_model is None or entry.base_model == base_model)
        )
        return tuple(sorted(entries, key=lambda item: item.identifier.casefold()))

    def compatible_loras(
        self, checkpoint_id: str, *, domain: str = "all", include_unavailable: bool = False
    ) -> tuple[CatalogueEntry, ...]:
        checkpoint = self.checkpoint(checkpoint_id, include_unavailable=include_unavailable)
        if checkpoint is None or checkpoint.base_model is None:
            return ()
        candidates = []
        for lora in self.view().loras:
            if lora.base_model != checkpoint.base_model:
                continue
            if domain != "all" and not lora.supports_domain(domain):
                continue
            if checkpoint.modes and lora.modes and not (checkpoint.modes & lora.modes):
                continue
            candidates.append(lora)
        return tuple(sorted(candidates, key=lambda item: ((item.name or item.identifier).casefold(), item.identifier)))
