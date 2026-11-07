"""Qt-independent connection check and its presentation mapping, shared by the Qt shell and tests."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

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
from dtflow.persistence.assets import AssetsBusyError
from dtflow.persistence.store import PersistenceError, StateBusyError
from dtflow.services.preflight import blocked


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
    level: str  # ok | warning | error | unknown
    headline: str
    detail: str = ""


def local_time(at_utc: str) -> str:
    """A recorded UTC instant as local wall-clock time to the second; unparseable values are shown as recorded."""
    try:
        return datetime.fromisoformat(at_utc).astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return str(at_utc)


def present_status(status: ConnectionStatus) -> Presentation:
    """Map supplied connection evidence to a status line; the network behavior that produced it is tested elsewhere."""
    details = dict(status.diagnostics)
    if details.get("server_message"):
        code = details.get("http_status") or details.get("status")
        prefix = f"{code}: " if code else ""
        return Presentation("error", "Connection rejected", prefix + details["server_message"])
    if status.transport == "failed":
        return Presentation("error", "Unreachable", f"Could not reach {status.endpoint}.")
    if status.authentication == "failed":
        return Presentation("error", "Authentication failed", f"{status.endpoint} rejected the credentials.")
    if status.transport == "ok" and status.authentication in ("ok", "not_applicable"):
        return Presentation("ok", "Connected", f"{status.endpoint}, checked at {local_time(status.checked_at_utc)}.")
    return Presentation("warning", "Status unknown", f"{status.endpoint} gave inconclusive evidence.")


def present_failure(exc: Exception) -> Presentation:
    """A check that raised: stable code plus an already-sanitized API explanation."""
    if isinstance(exc, ValidationError):
        return Presentation("error", "Invalid selection", str(exc))
    if isinstance(exc, BackendError):
        failure = exc.failure
        if dict(failure.diagnostics).get("transport"):
            return Presentation("error", "Check failed", _api_explanation(failure))
        if failure.category in ("credentials", "authentication"):
            return Presentation("error", "Credentials problem", failure.code)
        return Presentation("error", "Check failed", failure.code)
    if isinstance(exc, TimeoutError):
        return Presentation("error", "Timed out", "The check exceeded its deadline.")
    return Presentation("error", "Check failed", type(exc).__name__)


def present_outcome(outcome) -> Presentation:
    """A saved generation (``session.GenerationOutcome``); warnings such as the fake-fixture note stay visible."""
    count = len(outcome.output_paths)
    # Paths and the attempt id are listed once, in the result list; the line itself says what happened.
    detail = f"{count} image{'s' if count != 1 else ''} with reproducibility metadata."
    if outcome.warnings:
        detail += " " + " ".join(outcome.warnings)
    return Presentation("ok", "Saved", detail)


UNSUPPORTED_NAMES = (
    ("operation", "this operation"),
    ("sampler", "this sampler"),
    ("model", "the chosen model"),
    ("lora", "LoRA selection"),
    ("batch", "this batch size"),
)


def failure_reason(failure) -> str:
    """The original reason of a failure, provider wording kept: one rule for Generate, Queues, Scripts and Workflows.

    ``failure`` is a ``BackendFailure`` or its stored record (``{"code", "message", "diagnostics", ...}``). Only
    backend-projected response text (already credential-free) or DTFlow's own sentence is returned; the code is the
    last resort for records that carry no message. Nothing is inferred (a missing output never reads as "quota").
    """
    if isinstance(failure, Mapping):
        code, message, pairs = failure.get("code"), failure.get("message"), failure.get("diagnostics")
    else:
        code, message, pairs = failure.code, failure.message, failure.diagnostics
    try:
        details = dict(pairs) if isinstance(pairs, Mapping) else {str(pair[0]): pair[1] for pair in pairs or ()}
    except (TypeError, IndexError, KeyError):
        details = {}
    text = details.get("interpretation_message") or details.get("server_message") or message or code
    return str(text or "").strip()


def _api_explanation(failure) -> str:
    """Status code and original reason, for the connection check and asset actions."""
    details = dict(failure.diagnostics)
    code = details.get("http_status") or details.get("status") or failure.code
    return f"{code}: {failure_reason(failure)}"


def _sentence(text: str) -> str:
    return text if not text or text.endswith((".", "!", "?")) else text + "."


def present_generation_failure(exc: Exception) -> Presentation:
    """Show safe API explanations without changing the durable remote-outcome evidence."""
    if isinstance(exc, ValidationError):
        return Presentation("error", "Invalid request", str(exc))
    if isinstance(exc, BackendError):
        failure = exc.failure
        api = bool(dict(failure.diagnostics).get("transport"))
        # The original reason, as Queues, Scripts and Workflows show it; codes and attempt ids stay in details/logs.
        detail = failure_reason(failure)
        if failure.code.startswith("prepared_asset_"):
            asset = dict(failure.diagnostics).get("asset")
            if api and asset:
                detail = f"LoRA {asset}: {detail}"
            return Presentation(
                "error",
                "Prepared asset not validated",
                f"{_sentence(detail)} Nothing was uploaded or submitted." if api else detail,
            )
        if failure.remote_outcome == RemoteOutcome.UNKNOWN:
            return Presentation(
                "warning",
                "Outcome unknown",
                f"{_sentence(detail)} The request may have reached the server and may still complete. "
                "It is not replayed automatically.",
            )
        if failure.code == "unsupported_request" and failure.diagnostics:
            wanted = dict(UNSUPPORTED_NAMES)
            names = " or ".join(wanted.get(name, name) for name, _ in failure.diagnostics)
            hint = (
                " Refresh models to see the models this provider offers."
                if failure.diagnostics[0][0] == "model"
                else ""
            )
            return Presentation(
                "error", "Request rejected", f"This provider does not offer {names}.{hint} Nothing was dispatched."
            )
        if failure.category == ErrorCategory.VALIDATION and not api:
            # Diagnostic pairs (e.g. "phase: generate") are internal; they stay in the details box and logs.
            return Presentation("error", "Request rejected", f"{_sentence(detail)} Nothing was dispatched.")
        if failure.code == "originals_unavailable":
            return Presentation("error", "Generation not submitted", failure.message)
        if failure.code == "local_save_unavailable":
            return Presentation("error", "Generated image not fully saved", failure.message)
        if api:
            # PERMISSION_DENIED also covers throttling and request validation upstream.
            return Presentation("error", "Provider request failed", detail)
        if failure.category in (ErrorCategory.CREDENTIALS, ErrorCategory.AUTHENTICATION):
            return Presentation("error", "Credentials problem", detail)
        return Presentation("error", "Generation failed", detail)
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
    # The attempt id stays in the details box and the logs, not in the status line.
    if evidence.state == "succeeded":
        return Presentation("ok", "Completed before the stop", "Output was already saved.")
    if evidence.state == "save_failed":
        return Presentation("error", "Local stop requested", "The server finished but the image was not saved.")
    if evidence.remote_outcome == RemoteOutcome.UNKNOWN or evidence.state == "unknown":
        return Presentation(
            "warning",
            "Local stop requested",
            "Outcome unknown: the request may have reached the server and may still complete. "
            "Remote cancellation is not confirmed.",
        )
    return Presentation("warning", "Local stop requested", "Stopped before submission; nothing was sent.")


def present_preflight(diagnostics) -> Presentation:
    """Render shared preflight diagnostics as given; architecture, availability and application evidence stay separate."""
    if not diagnostics:
        return Presentation("unknown", "No LoRAs selected", "Compatibility is not checked.")
    lines = [
        f"{item.identifier}: architecture {item.architecture_match}, {item.availability} ({item.code}); "
        f"application {item.generation_evidence.removeprefix('application_')}"
        for item in diagnostics
    ]
    if blocked(diagnostics):
        level, headline = "error", "Blocked before dispatch"
    elif all(item.architecture_match == "match" for item in diagnostics):
        level, headline = "ok", "Architecture matches"
    else:
        level, headline = "unknown", "Compatibility not confirmed"
    return Presentation(level, headline, "\n".join(lines))


def present_asset_failure(exc: Exception) -> Presentation:
    """An asset action that raised; an interrupted or unconfirmed transfer is never shown as a plain failure."""
    if isinstance(exc, AssetsBusyError):
        return Presentation(
            "warning", "Assets busy", "Another DTFlow process is preparing assets. Nothing was uploaded or changed."
        )
    if isinstance(exc, BackendError) and exc.failure.remote_outcome == RemoteOutcome.UNKNOWN:
        detail = _api_explanation(exc.failure) if dict(exc.failure.diagnostics).get("transport") else exc.failure.code
        return Presentation(
            "warning",
            "Outcome unknown",
            f"{detail}. The file may have been stored. It is not retried; reconcile the record to check.",
        )
    if isinstance(exc, BackendError):
        if dict(exc.failure.diagnostics).get("transport"):
            return Presentation("error", "Asset action failed", _api_explanation(exc.failure))
        return Presentation("error", "Asset action failed", f"{exc.failure.code}. {exc.failure.message}")
    return present_generation_failure(exc)


def present_reconciliation(result) -> Presentation:
    """A fresh metadata-only server check (``AssetReconciliation``); it never uploads."""
    level = {"confirmed": "ok", "upload_required": "warning"}.get(result.status, "warning")
    return Presentation(level, "Reconciled: " + result.status.replace("_", " "), f"Evidence: {result.evidence}.")
