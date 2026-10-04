"""Failure construction and evidence mapping for the Draw Things integration.

Messages carry stable codes and status names only: never provider text, prompts, secrets or exception details.
"""

from __future__ import annotations

import asyncio

from grpclib import GRPCError, Status
from grpclib.exceptions import ProtocolError, StreamTerminatedError

from dtflow.backends.drawthings.tls import classify_tls_error
from dtflow.core.models import (
    BackendError,
    BackendFailure,
    ErrorCategory,
    Metadata,
    RemoteOutcome,
    SubmissionStage,
    ValidationError,
)

_AUTHENTICATION = (Status.UNAUTHENTICATED, Status.PERMISSION_DENIED)


def failure(
    category: ErrorCategory,
    code: str,
    message: str,
    request_id: str | None = None,
    stage: SubmissionStage = SubmissionStage.NOT_SUBMITTED,
    outcome: RemoteOutcome = RemoteOutcome.NOT_STARTED,
    **extra,
) -> BackendFailure:
    return BackendFailure(category, code, message, request_id, stage, outcome, **extra)


def error(*args, **kwargs) -> BackendError:
    return BackendError(failure(*args, **kwargs))


def diagnostics(**items: str) -> Metadata:
    """Credential-free diagnostic pairs; values are short codes, never provider text."""
    return tuple(sorted((key, str(value)) for key, value in items.items()))


def evidence(stage: SubmissionStage, *, trailers: bool = False) -> tuple[SubmissionStage, RemoteOutcome]:
    """Map what was observed to independent submission and remote-outcome evidence.

    Received trailers mean the server finished the RPC, but a terminal error status alone does not prove the work
    failed or stopped, so the remote outcome stays unknown once a request may have been sent.
    """
    if stage == SubmissionStage.NOT_SUBMITTED:
        return SubmissionStage.NOT_SUBMITTED, RemoteOutcome.NOT_STARTED
    if trailers:
        return SubmissionStage.TERMINAL_OBSERVED, RemoteOutcome.UNKNOWN
    return SubmissionStage.POSSIBLY_SUBMITTED, RemoteOutcome.UNKNOWN


def from_exception(
    exc: BaseException,
    *,
    phase: str,
    request_id: str | None = None,
    stage: SubmissionStage = SubmissionStage.NOT_SUBMITTED,
    trailers: bool = False,
) -> BackendFailure:
    """Classify a transport/protocol exception without exposing its text. Cancellation is never classified."""
    if isinstance(exc, asyncio.CancelledError):
        raise exc
    recorded_stage, outcome = evidence(stage, trailers=trailers and isinstance(exc, GRPCError))
    if isinstance(exc, ValidationError):
        # Wire validation runs inside the owned generation task, before any submission.
        return failure(
            ErrorCategory.VALIDATION,
            "invalid_generation_request",
            "Generation request is unsupported or invalid.",
            request_id,
            recorded_stage,
            outcome,
            diagnostics=diagnostics(phase=phase),
        )
    tls = classify_tls_error(exc)
    if tls is not None:
        # TLS verification happens before any request bytes exist.
        return failure(
            ErrorCategory.CONNECTIVITY,
            tls,
            "TLS verification or handshake failed.",
            request_id,
            diagnostics=diagnostics(phase=phase, tls=tls),
        )
    if isinstance(exc, GRPCError):
        status = exc.status
        code = "grpc_" + status.name.lower()
        category = (
            ErrorCategory.AUTHENTICATION
            if status in _AUTHENTICATION
            else ErrorCategory.CONNECTIVITY
            if status == Status.UNAVAILABLE
            else ErrorCategory.TIMEOUT
            if status == Status.DEADLINE_EXCEEDED
            else ErrorCategory.REMOTE_REJECTION
        )
        return failure(
            category,
            code,
            "Server finished the call with an error status.",
            request_id,
            recorded_stage,
            outcome,
            terminal_status=status.name if trailers else None,
            timeout_phase=phase if category == ErrorCategory.TIMEOUT else None,
            diagnostics=diagnostics(phase=phase, status=status.name, trailers="received" if trailers else "none"),
        )
    if isinstance(exc, TimeoutError):
        return failure(
            ErrorCategory.TIMEOUT,
            phase + "_deadline",
            "Backend operation deadline exceeded.",
            request_id,
            recorded_stage,
            outcome,
            timeout_phase=phase,
            diagnostics=diagnostics(phase=phase),
        )
    if isinstance(exc, (StreamTerminatedError, ProtocolError, OSError, EOFError)):
        return failure(
            ErrorCategory.CONNECTIVITY,
            "connect_failed" if stage == SubmissionStage.NOT_SUBMITTED else "connection_lost",
            "Connection to the server failed or was lost.",
            request_id,
            recorded_stage,
            outcome,
            diagnostics=diagnostics(phase=phase, kind=type(exc).__name__),
        )
    return failure(
        ErrorCategory.PROTOCOL,
        "unexpected_provider_failure",
        "Unexpected failure in the provider integration.",
        request_id,
        recorded_stage,
        outcome,
        diagnostics=diagnostics(phase=phase, kind=type(exc).__name__),
    )
