"""Shared LoRA preflight: structured diagnostics from inventory evidence, no guessed compatibility.

Architecture match, availability and generation evidence stay separate. Only a confirmed architecture mismatch or a
confirmed absence blocks dispatch; a prepared LoRA without a version is rejected. Other unknown metadata stays unknown,
not labeled compatible. The official engine's base/refiner and Kandinsky/SD-v1 exception rules are not pinned yet, so this
service does not model them: every selection is a whole-model (``All``) LoRA on a base model.
"""

import logging
from dataclasses import replace

from dtflow.core.model_profiles import exact_model_spec
from dtflow.core.models import (
    BackendError,
    BackendFailure,
    ErrorCategory,
    InventorySnapshot,
    PreflightDiagnostic,
    ResolvedGenerationRequest,
)
from dtflow.diagnostics import event as diagnostic_event
from dtflow.services.catalogue import bundled_exact_version, bundled_family_versions
from dtflow.services.dynamic_prompts import PROMPT_TAGS_KEY, decode_runs, encode_runs

BLOCKING = ("architecture_mismatch", "not_available", "prepared_architecture_unknown")
# Successful generation never proves a LoRA was applied, so the evidence level is fixed until the backend reports it.
UNCONFIRMED = "application_unconfirmed"
log = logging.getLogger(__name__)


def checkpoint_exact_version(model_id: str, snapshot: InventorySnapshot | None = None) -> str | None:
    """Prefer exact published and Echo versions; use only unambiguous bundled identities."""
    spec = exact_model_spec(model_id)
    version = spec.get("version") if spec is not None else None
    if isinstance(version, str) and version:
        return version
    if snapshot is not None:
        entry = next(
            (item for item in snapshot.entries if item.identifier == model_id and item.kind == "model"),
            None,
        )
        if entry is not None and entry.architecture:
            return entry.architecture
    return bundled_exact_version(model_id)


def checkpoint_picker_versions(model_id: str, snapshot: InventorySnapshot | None = None) -> frozenset[str]:
    """Exact versions whose LoRAs a picker offers for this checkpoint; empty when its family is unknown.

    The exact version when known, else what its bundled catalogue family allows. Picker filtering only.
    """
    exact = checkpoint_exact_version(model_id, snapshot)
    return frozenset((exact,)) if exact is not None else bundled_family_versions(model_id)


def preflight_loras(
    request: ResolvedGenerationRequest,
    snapshot: InventorySnapshot,
    *,
    checkpoint_version: str | None = None,
) -> tuple[PreflightDiagnostic, ...]:
    """Return one diagnostic per selected LoRA, in selection order."""
    by_id = {entry.identifier: entry for entry in snapshot.entries}
    prepared = {item.identifier: item for item in request.prepared_assets}
    model = by_id.get(request.model_id)
    # Exact-file metadata takes priority; Echo model versions are already provider IDs.
    # Never infer an exact version from a broad checkpoint catalogue grouping.
    base = checkpoint_version or (model.architecture if model is not None and model.kind == "model" else None)
    source = f"{snapshot.backend_id}:{snapshot.observed_at_utc}"
    diagnostics = []
    for selection in request.loras:
        entry = by_id.get(selection.identifier)
        asset = prepared.get(selection.identifier)
        if asset is not None:
            # Canonical local metadata resolves explicit user choice over file evidence.
            # A receipt proves availability, not that the LoRA affected the output.
            if asset.family is None:
                match, code = "unknown", "prepared_architecture_unknown"
            elif base is not None:
                match = "match" if asset.family == base else "mismatch"
                code = "ok" if match == "match" else "architecture_mismatch"
            else:
                match, code = "unknown", "architecture_unknown"
            diagnostics.append(
                PreflightDiagnostic(
                    selection.identifier,
                    match,
                    "prepared",
                    UNCONFIRMED,
                    code,
                    asset.family_source if asset.family_source in ("file", "user") else source,
                    model_family=base,
                    lora_family=asset.family,
                )
            )
            continue
        if entry is None and snapshot.backend_id.endswith("cloud"):
            # DT+ Echo is not an authoritative LoRA listing (it may list files without metadata, or none), so a LoRA it
            # does not name is unconfirmed, not confirmed absent.
            diagnostics.append(
                PreflightDiagnostic(
                    selection.identifier, "unknown", "unknown", UNCONFIRMED, "availability_unknown", source
                )
            )
            continue
        if entry is not None and entry.kind == "unknown":
            # A listed file the server did not classify (file list without metadata): present, architecture unknown.
            diagnostics.append(
                PreflightDiagnostic(
                    selection.identifier, "unknown", "present", UNCONFIRMED, "architecture_unknown", source
                )
            )
            continue
        if entry is None or entry.kind != "lora":
            diagnostics.append(
                PreflightDiagnostic(selection.identifier, "unknown", "absent", UNCONFIRMED, "not_available", source)
            )
            continue
        if entry.architecture is None or base is None:
            match, code = "unknown", "architecture_unknown"
        elif entry.architecture == base:
            match, code = "match", "ok"
        else:
            match, code = "mismatch", "architecture_mismatch"
        diagnostics.append(
            PreflightDiagnostic(
                selection.identifier,
                match,
                "present",
                UNCONFIRMED,
                code,
                source,
                model_family=base,
                lora_family=entry.architecture,
            )
        )
    return tuple(diagnostics)


def blocked(diagnostics: tuple[PreflightDiagnostic, ...]) -> tuple[PreflightDiagnostic, ...]:
    return tuple(item for item in diagnostics if item.code in BLOCKING)


def raise_if_blocked(request: ResolvedGenerationRequest, diagnostics: tuple[PreflightDiagnostic, ...]) -> None:
    problems = blocked(diagnostics)
    for item in diagnostics:
        if item.code in BLOCKING or item.code == "architecture_unknown":
            diagnostic_event(
                log,
                "lora.compatibility_blocked" if item.code in BLOCKING else "lora.compatibility_unknown",
                level=logging.WARNING,
                data={
                    "reason": item.code,
                    "checkpoint_version": item.model_family or "unknown",
                    "lora_version": item.lora_family or "unknown",
                    "outcome": "not_submitted" if item.code in BLOCKING else "unverified",
                },
            )
    if problems:
        raise BackendError(
            BackendFailure(
                ErrorCategory.VALIDATION,
                "lora_preflight_failed",
                "LoRA preflight rejected the request before dispatch. "
                + "; ".join(
                    f"{item.identifier}: LoRA family {item.lora_family} does not match "
                    f"checkpoint family {item.model_family} ({request.model_id})."
                    if item.code == "architecture_mismatch" and item.lora_family and item.model_family
                    else f"{item.identifier}: prepared LoRA architecture is unknown; use a LoRA with a detected version."
                    if item.code == "prepared_architecture_unknown"
                    else f"{item.identifier}: {item.code}."
                    for item in problems
                ),
                request.request_id,
                diagnostics=tuple((item.identifier, item.code) for item in problems),
            )
        )


def apply_trigger_prefixes(
    request: ResolvedGenerationRequest, snapshot: InventorySnapshot
) -> ResolvedGenerationRequest:
    """Explicitly prepend each selected LoRA's server-supplied trigger prefix to the prompt, once, in selection order."""
    by_id = {entry.identifier: entry for entry in snapshot.entries}
    prefixes = [
        entry.trigger_prefix.strip()
        for selection in request.loras
        if (entry := by_id.get(selection.identifier)) is not None
        and entry.trigger_prefix
        and entry.trigger_prefix.strip()
    ]
    prefixes = [prefix for prefix in prefixes if prefix not in request.prompt]
    if not prefixes:
        return request
    prompt = " ".join((*prefixes, request.prompt)).strip()
    evidence = []
    for key, value in request.resolution_evidence:
        if key == PROMPT_TAGS_KEY:  # the prefixes push the variation runs along; the checksum follows the new text
            shift = len(" ".join(prefixes)) + 1 - (len(request.prompt) - len(request.prompt.lstrip()))
            runs = tuple((start + shift, length) for start, length in decode_runs(request.prompt, value))
            value = encode_runs(prompt, runs)
        evidence.append((key, value))
    evidence.append(("trigger_prefixes", "|".join(prefixes)))
    return replace(request, prompt=prompt, resolution_evidence=tuple(evidence))
