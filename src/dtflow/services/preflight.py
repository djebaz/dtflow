"""Shared LoRA preflight: structured diagnostics from inventory evidence, no guessed compatibility.

Architecture match, availability and generation evidence stay separate. Only a confirmed architecture mismatch or a
confirmed absence blocks dispatch; unknown metadata stays unknown and is reported, never silently omitted or labeled
compatible. The official engine's base/refiner and Kandinsky/SD-v1 exception rules are not pinned yet, so this
service does not model them: every selection is a whole-model (``All``) LoRA on a base model.
"""

from dataclasses import replace

from dtflow.core.models import (
    BackendError,
    BackendFailure,
    ErrorCategory,
    InventorySnapshot,
    PreflightDiagnostic,
    ResolvedGenerationRequest,
)

BLOCKING = ("architecture_mismatch", "not_available")
# Successful generation never proves a LoRA was applied, so the evidence level is fixed until the backend reports it.
UNCONFIRMED = "application_unconfirmed"


def preflight_loras(request: ResolvedGenerationRequest, snapshot: InventorySnapshot) -> tuple[PreflightDiagnostic, ...]:
    """Return one diagnostic per selected LoRA, in selection order."""
    by_id = {entry.identifier: entry for entry in snapshot.entries}
    prepared = {item.identifier for item in request.prepared_assets}
    model = by_id.get(request.model_id)
    source = f"{snapshot.backend_id}:{snapshot.observed_at_utc}"
    diagnostics = []
    for selection in request.loras:
        entry = by_id.get(selection.identifier)
        if (entry is None or entry.kind != "lora") and selection.identifier in prepared:
            # A server-validated prepared reference is availability evidence; architecture stays unknown, never matched.
            diagnostics.append(
                PreflightDiagnostic(
                    selection.identifier, "unknown", "prepared", UNCONFIRMED, "architecture_unknown", source
                )
            )
            continue
        if entry is None or entry.kind != "lora":
            diagnostics.append(
                PreflightDiagnostic(selection.identifier, "unknown", "absent", UNCONFIRMED, "not_available", source)
            )
            continue
        base = model.architecture if model is not None and model.kind == "model" else None
        if entry.architecture is None or base is None:
            match, code = "unknown", "architecture_unknown"
        elif entry.architecture == base:
            match, code = "match", "ok"
        else:
            match, code = "mismatch", "architecture_mismatch"
        diagnostics.append(PreflightDiagnostic(selection.identifier, match, "present", UNCONFIRMED, code, source))
    return tuple(diagnostics)


def blocked(diagnostics: tuple[PreflightDiagnostic, ...]) -> tuple[PreflightDiagnostic, ...]:
    return tuple(item for item in diagnostics if item.code in BLOCKING)


def raise_if_blocked(request: ResolvedGenerationRequest, diagnostics: tuple[PreflightDiagnostic, ...]) -> None:
    problems = blocked(diagnostics)
    if problems:
        raise BackendError(
            BackendFailure(
                ErrorCategory.VALIDATION,
                "lora_preflight_failed",
                "LoRA preflight rejected the request before dispatch.",
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
    evidence = (*request.resolution_evidence, ("trigger_prefixes", "|".join(prefixes)))
    return replace(request, prompt=" ".join((*prefixes, request.prompt)).strip(), resolution_evidence=evidence)
