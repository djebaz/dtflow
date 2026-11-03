"""Bounded read-only script projection of existing inventory/classification evidence.

No backend creation, refresh, network, credential or filesystem discovery here.
Prepared means a historical confirmed local receipt, not fresh server validation.
"""

from pathlib import Path

from dtflow.core.models import InventorySnapshot, PreparedAsset, ValidationError
from dtflow.services.catalogue import Catalogue


def _symbolic(value: str) -> bool:
    return not any(char in value for char in "/\\:") and not any(ord(char) < 32 for char in value)


def script_lora_inventory(
    state: Path, snapshot: InventorySnapshot, assets: tuple[PreparedAsset, ...], model: str
) -> dict:
    if len(model) > 1024 or not _symbolic(model):
        raise ValidationError("The selected model needs a bounded symbolic identifier for script inventory.")
    catalogue = Catalogue(state)
    definitions = {entry.identifier: entry for entry in catalogue.lora_definitions()}
    raw = {entry.identifier: entry for entry in snapshot.entries}
    prepared = {asset.identifier for asset in assets}
    known = set(definitions) | prepared | {entry.identifier for entry in snapshot.entries if entry.kind == "lora"}
    if len(known) > 4096:
        raise ValidationError("The script inventory exceeds its 4096-LoRA bound.")
    omitted = {identifier for identifier in known if not _symbolic(identifier)}
    known -= omitted
    checkpoint = catalogue.checkpoint(model, include_unavailable=True)
    base = raw.get(model)
    rows = []
    for identifier in sorted(known):
        if len(identifier) > 1024:
            raise ValidationError("A LoRA identifier exceeds the script inventory bound.")
        definition, observed = definitions.get(identifier), raw.get(identifier)
        compatibility = "unknown"
        compatibility_source = "unknown"
        if base is not None and observed is not None and base.architecture and observed.architecture:
            compatibility = "match" if base.architecture == observed.architecture else "mismatch"
            compatibility_source = "backend_metadata"
        elif checkpoint is not None and definition is not None and checkpoint.base_model and definition.base_model:
            compatibility = "match" if checkpoint.base_model == definition.base_model else "mismatch"
            compatibility_source = "catalogue"
        if definition is not None and definition.modes and "t2i" not in definition.modes:
            compatibility = "mismatch"
            compatibility_source = "catalogue_modes"
        listed = observed is not None and observed.kind in ("lora", "unknown")
        ready = listed or identifier in prepared
        reason = "metadata_mismatch" if compatibility == "mismatch" else "unconfirmed_availability" if not ready else ""
        name = (definition.name if definition else None) or (observed.display_name if observed else None) or identifier
        if not _symbolic(name):
            name = identifier
        rows.append(
            {
                "file": identifier,
                "name": name[:512],
                "listed": listed,
                "prepared": identifier in prepared,
                "compatibility": compatibility,
                "compatibilitySource": compatibility_source,
                "eligible": ready and compatibility != "mismatch",
                "reason": reason,
            }
        )
    return {
        "model": model,
        "observedAt": snapshot.observed_at_utc,
        "cached": True,
        "items": rows,
        "total": len(rows),
        "omitted": len(omitted),
        "notice": "Listed/prepared are observations, not verified current availability or runtime compatibility.",
    }
