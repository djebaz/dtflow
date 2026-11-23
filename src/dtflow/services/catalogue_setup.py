"""Qt-free catalogue setup projection; read and compute on a worker, publish as one snapshot."""

from dataclasses import dataclass
from pathlib import Path

from dtflow.core.models import InventorySnapshot
from dtflow.services.catalogue import Catalogue, CatalogueEntry, catalogue_family_versions
from dtflow.services.preflight import checkpoint_picker_versions

UNCLASSIFIED_FAMILY = "__unclassified__"


@dataclass(frozen=True, slots=True)
class CatalogueChoices:
    catalogue: Catalogue
    families: tuple[tuple[str, str], ...]
    family: str | None
    checkpoints: tuple[str, ...]
    checkpoint: str
    include_niche: bool
    loras: tuple[CatalogueEntry, ...] | None
    versions: frozenset[str]


def family_picker_versions(family: str | None) -> frozenset[str]:
    return catalogue_family_versions(family) if family != UNCLASSIFIED_FAMILY else frozenset()


def lora_candidates(catalogue, inventory, *, checkpoint, family, domain, include_cloud):
    """One candidate rule for worker setup and subsequent in-memory checkpoint filtering."""
    model = catalogue.checkpoint(checkpoint, include_unavailable=include_cloud)
    versions = checkpoint_picker_versions(checkpoint, inventory) if checkpoint else family_picker_versions(family)
    entries = (
        catalogue.compatible_loras(checkpoint, domain=domain, include_unavailable=include_cloud)
        if versions and model is not None and model.base_model is not None
        else None
    )
    return entries, versions


def read_choices(
    state_dir: Path,
    inventory: InventorySnapshot | None,
    *,
    domain: str,
    include_cloud: bool,
    include_niche: bool,
    family: str | None,
    checkpoint: str,
    restore_niche: bool,
    preserve_selection: bool,
    follow_checkpoint: bool,
) -> CatalogueChoices:
    catalogue = Catalogue.read_snapshot(state_dir, inventory)
    preferred = catalogue.checkpoint(checkpoint) if checkpoint else None
    include_niche = include_niche or bool(restore_niche and preferred and preferred.tier == "niche")
    families = catalogue.families(domain=domain, include_niche=include_niche, include_unavailable=include_cloud)
    unclassified = set(catalogue.view().unclassified_checkpoints)
    if unclassified:
        families = (*families, (UNCLASSIFIED_FAMILY, "Unclassified"))
    available_families = {key for key, _ in families}
    if family not in available_families:
        family = preferred.base_model if preferred else UNCLASSIFIED_FAMILY if checkpoint in unclassified else None
        if family not in available_families:
            family = families[0][0] if families else None
    kept = catalogue.checkpoint(checkpoint, include_unavailable=True) if checkpoint else None
    if follow_checkpoint and kept and kept.base_model in available_families:
        family = kept.base_model
    entries = (
        ()
        if family == UNCLASSIFIED_FAMILY
        else catalogue.checkpoints(
            domain=domain, base_model=family, include_niche=include_niche, include_unavailable=include_cloud
        )
    )
    selectable = (
        tuple(sorted(unclassified, key=str.casefold))
        if family == UNCLASSIFIED_FAMILY
        else tuple(entry.identifier for entry in entries)
    )
    outside = bool(not preserve_selection and kept and kept.base_model and family and kept.base_model != family)
    if outside or not (
        preserve_selection
        or inventory is None
        or checkpoint in selectable
        or checkpoint in unclassified
        or (checkpoint and catalogue.checkpoint(checkpoint) is None)
    ):
        checkpoint = ""
    loras, versions = lora_candidates(
        catalogue, inventory, checkpoint=checkpoint, family=family, domain=domain, include_cloud=include_cloud
    )
    return CatalogueChoices(catalogue, families, family, selectable, checkpoint, include_niche, loras, versions)
