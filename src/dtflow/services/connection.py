"""Qt-independent connection check and its presentation mapping, shared by the Qt shell and tests."""

import asyncio
from dataclasses import dataclass

from dtflow.composition import BackendSelection, create_backend
from dtflow.core.models import BackendError, ConnectionStatus, CredentialProvider, TimeoutPolicy, ValidationError


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
