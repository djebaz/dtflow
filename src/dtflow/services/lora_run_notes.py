"""Plain-English LoRA notes for runs (#388): why a recovery check failed and which custom LoRAs are unprepared.

Stored records and events keep the technical identifier (the managed filename). Presenters turn identifiers into
``Short[Tag]`` labels when they display a note (``lora_naming.relabel_text``); diagnostic logs use only
``lora_naming.log_label``. Nothing here calls a provider, sends a file or blocks a dispatch.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from dtflow.core.models import BackendError, PreflightDiagnostic, ResolvedGenerationRequest

#: Event kinds that carry the affected LoRAs under ``loras`` (technical identifiers) for the activity log.
RECOVERY_FAILED = "asset_recovery_failed"
REFRESH_PAUSE = "lora_refresh_pause"
FAILURE_PAUSE = "lora_failure_pause"
UNPREPARED = "lora_unprepared"


def request_identifiers(request: ResolvedGenerationRequest) -> list[str]:
    """The LoRAs a request selected, in selection order, without repeats."""
    return list(dict.fromkeys(item.identifier for item in request.loras))


def unprepared_loras(request: ResolvedGenerationRequest, diagnostics: Iterable[PreflightDiagnostic]) -> list[str]:
    """Selected cloud LoRAs that DT+ does not list and that have no bound prepared receipt: no custom file is sent."""
    bound = {item.identifier for item in request.prepared_assets}
    return [
        item.identifier for item in diagnostics if item.code == "availability_unknown" and item.identifier not in bound
    ]


def unprepared_message(identifier: str) -> str:
    """One warning line; application stays unconfirmed even when the generation succeeds."""
    return (
        f"{identifier}: not prepared, so no custom file is sent and DT+ application is unconfirmed. "
        "If this is a custom LoRA, use Prepare."
    )


def recovery_failure_text(exc: BaseException) -> str:
    """The meaningful reason a recovery check stopped, kept as the backend gave it (not a generic 'restore failed')."""
    if isinstance(exc, BackendError):
        failure = exc.failure
        message = (failure.message or "").strip()
        return f"{message} ({failure.code})" if message else str(failure.code)
    text = str(exc).strip()
    return f"{text} ({type(exc).__name__})" if text else type(exc).__name__


def recovery_failed_detail(identifiers: Sequence[str], exc: BaseException) -> dict[str, Any]:
    return {"reason": "restore_failed", "loras": list(identifiers), "detail": recovery_failure_text(exc)}


def recovery_failed_message(identifiers: Sequence[str], detail: str, *, pauses: bool = True) -> str:
    """Run-level sentence for a recovery check that failed; relabelled for display only.

    Queue and Workflow pause, so they say so; Scripts do not pause (they show their own Retry form or fail the job)."""
    names = ", ".join(identifiers) or "a selected LoRA"
    reason = f" {detail}." if detail and not detail.endswith(".") else (f" {detail}" if detail else "")
    follow = (
        "Paused. Use Retry once the cause is fixed, or Prepare the LoRA again."
        if pauses
        else "Retry once the cause is fixed, or Prepare the LoRA again."
    )
    return f"Checking {names} on DT+ failed.{reason} {follow}"


def pause_message(kind: str, identifiers: Sequence[str]) -> str:
    names = ", ".join(identifiers) or "a selected LoRA"
    if kind == REFRESH_PAUSE:
        return (
            f"Paused: {names} changed on disk and could not be prepared again, so nothing was sent with the old "
            "file. Use Prepare, then Retry."
        )
    return (
        f"Paused: several requests in a row ended with DT+'s generic error while using {names}. "
        "The file may be unusable on DT+. Use Prepare again, then Retry."
    )
