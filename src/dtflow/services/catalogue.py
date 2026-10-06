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


class Catalogue:
    """Static classification plus one active provider's availability."""

    def __init__(self, state_dir: Path, *, bundled_dir: Path = CATALOGUE_DIR):
        self.state_dir = Path(state_dir)
        self.bundled_dir = Path(bundled_dir)
        self._prime = self._load("Models_prime.json", kind="checkpoint", tier="prime")
        self._niche = self._load("Models_niche.json", kind="checkpoint", tier="niche")
        self._bundled_loras = self._load("Loras.json", kind="lora")
        self._custom_loras = self._load("Custom_loras.json", kind="lora", source="custom")
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
        checkpoints = tuple(entry for entry in (*self._prime, *self._niche) if entry.identifier in available)
        loras = tuple(
            entry
            for entry in (*self._bundled_loras, *self._custom_loras, *self.user_loras())
            if entry.identifier in available
        )
        known_entries = (*self._prime, *self._niche)
        known = {entry.identifier for entry in known_entries}
        unknown = ()
        if self._inventory is not None:
            metadata = dict(self._inventory.diagnostics).get("model_metadata") == "present"
            raw_unknown = {
                entry.identifier
                for entry in self._inventory.entries
                if entry.identifier not in known
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
        self, *, domain: str = "all", base_model: str | None = None, include_niche: bool = False
    ) -> tuple[CatalogueEntry, ...]:
        if domain not in DOMAINS:
            raise ValueError("Catalogue domain must be all, image or video.")
        entries = (
            entry
            for entry in self.view().checkpoints
            if (entry.tier == "prime" or include_niche)
            and entry.supports_domain(domain)
            and (base_model is None or entry.base_model == base_model)
        )
        return tuple(sorted(entries, key=lambda item: (item.tier == "niche", item.identifier.casefold())))

    def families(self, *, domain: str = "all", include_niche: bool = False) -> tuple[tuple[str, str], ...]:
        present = {
            entry.base_model
            for entry in self.checkpoints(domain=domain, include_niche=include_niche)
            if entry.base_model is not None
        }
        ordered = [family for family in FAMILY_ORDER if family in present]
        ordered.extend(sorted(present - set(ordered)))
        return tuple((family, FAMILY_LABELS.get(family, family)) for family in ordered)

    def checkpoint(self, identifier: str) -> CatalogueEntry | None:
        return next((entry for entry in self.view().checkpoints if entry.identifier == identifier), None)

    def compatible_loras(self, checkpoint_id: str, *, domain: str = "all") -> tuple[CatalogueEntry, ...]:
        checkpoint = self.checkpoint(checkpoint_id)
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
