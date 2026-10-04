"""Qt-independent connection check and its presentation mapping, shared by the Qt shell and tests."""

import asyncio
from dataclasses import dataclass

from dtflow.composition import BackendSelection, create_backend
from dtflow.core.models import (
    BackendError,
    ConnectionStatus,
    CredentialProvider,
    ErrorCategory,
    RemoteOutcome,
    TimeoutPolicy,
    ValidationError,
)
from dtflow.persistence.store import PersistenceError, StateBusyError


async def check_connection(
    selection: BackendSelection,
    *,
    saved: CredentialProvider | None = None,
    timeouts: TimeoutPolicy | None = None,
) -> ConnectionStatus:
    """Open the explicitly selected backend, run its bounded non-generative check and always close it."""
    policy = timeouts or TimeoutPolicy()
    backend = create_backend(selection, timeouts=policy, saved=saved)
    try:
        async with asyncio.timeout(policy.connect):
            await backend.open()
        return await backend.check_connection()
    finally:
        async with asyncio.timeout(policy.cleanup):
            await backend.aclose()


@dataclass(frozen=True, slots=True)
class Presentation:
    level: str  # ok | warning | error
    headline: str
    detail: str = ""


def present_status(status: ConnectionStatus) -> Presentation:
    """Map supplied connection evidence to a status line; the network behavior that produced it is tested elsewhere."""
    if status.transport == "failed":
        return Presentation("error", "Unreachable", f"Could not reach {status.endpoint}.")
    if status.authentication == "failed":
        return Presentation("error", "Authentication failed", f"{status.endpoint} rejected the credentials.")
    if status.transport == "ok" and status.authentication in ("ok", "not_applicable"):
        return Presentation("ok", "Connected", f"{status.endpoint} (checked {status.checked_at_utc}).")
    return Presentation("warning", "Status unknown", f"{status.endpoint} gave inconclusive evidence.")


def present_failure(exc: Exception) -> Presentation:
    """A check that raised: stable code only, never provider text."""
    if isinstance(exc, ValidationError):
        return Presentation("error", "Invalid selection", str(exc))
    if isinstance(exc, BackendError):
        failure = exc.failure
        if failure.category in ("credentials", "authentication"):
            return Presentation("error", "Credentials problem", failure.code)
        return Presentation("error", "Check failed", failure.code)
    if isinstance(exc, TimeoutError):
        return Presentation("error", "Timed out", "The check exceeded its deadline.")
    return Presentation("error", "Check failed", type(exc).__name__)


def present_outcome(outcome) -> Presentation:
    """A saved generation (``session.GenerationOutcome``); warnings such as the fake-fixture note stay visible."""
    count = len(outcome.output_paths)
    detail = f"Attempt {outcome.attempt_id[:8]} saved {count} output(s). Metadata: {outcome.metadata_path}"
    if outcome.warnings:
        detail += " " + " ".join(outcome.warnings)
    return Presentation("ok", "Saved", detail)


def present_generation_failure(exc: Exception) -> Presentation:
    """A generation that raised: stable codes only; an unknown remote outcome is never shown as a plain failure."""
    if isinstance(exc, ValidationError):
        return Presentation("error", "Invalid request", str(exc))
    if isinstance(exc, BackendError):
        failure = exc.failure
        if failure.remote_outcome == RemoteOutcome.UNKNOWN:
            return Presentation(
                "warning",
                "Outcome unknown",
                f"{failure.code}: the request may have reached the server and may still complete. "
                "It is not replayed automatically; inspect the attempt before acting.",
            )
        if failure.category == ErrorCategory.VALIDATION:
            return Presentation("error", "Request rejected", f"{failure.code}. Nothing was dispatched.")
        if failure.category in (ErrorCategory.CREDENTIALS, ErrorCategory.AUTHENTICATION):
            return Presentation("error", "Credentials problem", failure.code)
        return Presentation("error", "Generation failed", failure.code)
    if isinstance(exc, StateBusyError):
        return Presentation(
            "warning", "State is busy", "Another DTFlow process owns the state directory. Nothing was dispatched."
        )
    if isinstance(exc, PersistenceError):
        return Presentation("error", "Local state error", "Execution state could not be read or committed.")
    if isinstance(exc, TimeoutError):
        return Presentation("error", "Timed out", "The operation exceeded its deadline.")
    return Presentation("error", "Generation failed", type(exc).__name__)


def present_interruption(evidence) -> Presentation:
    """Wording after a local stop comes from recorded evidence (``session.AttemptEvidence``), never from the click."""
    if evidence is None:
        return Presentation("warning", "Local stop requested", "No attempt had been recorded; nothing was dispatched.")
    note = f"Attempt {evidence.attempt_id[:8]}."
    if evidence.state == "succeeded":
        return Presentation("ok", "Completed before the stop", f"{note} Output was already saved.")
    if evidence.state == "save_failed":
        return Presentation(
            "error", "Local stop requested", f"{note} The server finished but saving did not; inspect the attempt."
        )
    if evidence.remote_outcome == RemoteOutcome.UNKNOWN or evidence.state == "unknown":
        return Presentation(
            "warning",
            "Local stop requested",
            f"{note} Outcome unknown: the request may have reached the server and may still complete. "
            "Remote cancellation is not confirmed.",
        )
    return Presentation("warning", "Local stop requested", f"{note} Stopped before submission; nothing was sent.")
