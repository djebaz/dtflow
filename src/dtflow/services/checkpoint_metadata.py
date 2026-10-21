"""Read-only checkpoint details composed from independent DTFlow catalogue authorities.

Published technical specifications require an exact checkpoint file match. A
provider inventory is an observation, not authorization to generate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from dtflow.core.model_profiles import exact_model_spec, has_own_profile, profile_for
from dtflow.core.models import InventorySnapshot
from dtflow.services.catalogue import Catalogue, FAMILY_LABELS

_VARIANT = re.compile(r"_(f16|f32|bf16|q[0-9]+p|i[0-9]+x|[0-9]+bit)\\.ckpt$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CheckpointMetadata:
    """Immutable display projection; never authorizes or changes generation."""

    identifier: str
    fields: tuple[tuple[str, str, str], ...]
    published_spec_json: str | None

    def as_text(self) -> str:
        """Return provenance-labelled, scrollable text for the shared inspector."""
        return "\\n".join(f"{label}: {value}  [Source: {source}]" for label, value, source in self.fields)


def resolve_checkpoint_metadata(
    identifier: str,
    *,
    catalogue: Catalogue,
    inventory: InventorySnapshot | None,
) -> CheckpointMetadata:
    """Resolve an exact file across independent catalogues without network access.

    Missing published metadata is left unknown; a derived model-description
    fallback is deliberately not a source for this inspector.
    """
    rows: list[tuple[str, str, str]] = [("Checkpoint", identifier or "Not selected", "Selection")]
    if not identifier:
        return CheckpointMetadata(identifier, tuple(rows), None)

    classification = catalogue.checkpoint(identifier, include_unavailable=True)
    family = classification.base_model if classification else None
    rows.append((
        "Family",
        FAMILY_LABELS.get(family, family) if family else "Unknown",
        "DTFlow classification" if family else "No classification",
    ))
    rows.append((
        "Catalogue tier",
        classification.tier or "Unspecified" if classification else "Unknown",
        "DTFlow classification" if classification else "No classification",
    ))
    rows.append((
        "Declared modes",
        ", ".join(sorted(classification.modes)) if classification and classification.modes else "Unknown",
        "DTFlow classification" if classification else "No classification",
    ))
    variant = _VARIANT.search(identifier)
    rows.append((
        "Filename format suffix",
        variant.group(1).upper() if variant else "Not identified",
        "Checkpoint filename only",
    ))

    spec = exact_model_spec(identifier)
    published = None
    if spec is not None:
        published = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for name, value in sorted(spec.items()):
            if name == "file":
                continue
            display = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
            rows.append((f"Technical / {name}", str(display), "Draw Things community metadata (exact file)"))
    else:
        rows.append(("Technical description", "Not available for this exact checkpoint", "Draw Things community metadata"))

    profile = profile_for(identifier, family)
    if profile is not None:
        basis = "DTFlow exact model profile" if has_own_profile(identifier) else "DTFlow family default profile"
        rows.extend((
            ("Suggested steps", str(profile.steps), basis),
            ("Suggested guidance", f"{profile.guidance:g}", basis),
            ("Suggested sampler", profile.sampler.value, basis),
        ))
    else:
        rows.append(("Suggested settings", "Not available", "DTFlow model profiles"))

    if inventory is None:
        rows.append(("Provider listing", "Not observed", "Provider inventory"))
    else:
        listed = any(entry.identifier == identifier for entry in inventory.entries)
        status = "Listed in last observation" if listed else "Not listed in last observation (not proof of unavailability)"
        rows.append((
            "Provider listing",
            f"{status}; {inventory.backend_id}; observed {inventory.observed_at_utc}",
            "Provider Echo / retained inventory",
        ))
    return CheckpointMetadata(identifier, tuple(rows), published)
