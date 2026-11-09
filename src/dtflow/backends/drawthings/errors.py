"""Failure construction and evidence mapping for the Draw Things integration.

Stable codes retain their meaning; API explanations cross the boundary only through shared bounded privacy projection.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from grpclib import GRPCError, Status
from grpclib.exceptions import ProtocolError, StreamTerminatedError

from dtflow.backends.drawthings.tls import classify_tls_error
from dtflow.diagnostics import active_cloud_key_data, api_error_data, event
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
log = logging.getLogger(__name__)


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
    """Credential-free pairs; any response text must already have passed api_error_data."""
    return tuple(sorted((key, str(value)) for key, value in items.items()))


def api_failure(*args, operation: str, transport: str, response=None, elapsed_ms=None, **kwargs) -> BackendFailure:
    """One API-failure observation, before wrapping/presentation can discard evidence."""
    problem = failure(*args, **kwargs)
    details = dict(problem.diagnostics)
    details.update(response or {})
    details.update(operation=operation, transport=transport)
    # Endpoint names are identifiers, not paths for the shared path-redaction policy.
    details["operation"] = operation.strip("/").replace("/", ".")
    if elapsed_ms is not None:
        details["elapsed_ms"] = round(elapsed_ms)
    interpretation_failed = problem.category == ErrorCategory.PROTOCOL and str(details.get("http_status")) == "200"
    if interpretation_failed:
        # Keep DTFlow's interpretation reason distinct from a server message such as "ok".
        details["interpretation_message"] = problem.message
    problem = replace(
        problem,
        message=problem.message if interpretation_failed else details.get("server_message") or problem.message,
        diagnostics=diagnostics(**details),
    )
    event(
        log,
        "backend.response_unusable" if interpretation_failed else "backend.api_failed",
        level=logging.WARNING,
        data={
            **details,
            "code": problem.code,
            "category": problem.category.value,
            "submission_stage": problem.submission_stage.value,
            "remote_outcome": problem.remote_outcome.value,
            **({"request_id": problem.request_id} if problem.request_id else {}),
            **active_cloud_key_data(),
        },
    )
    return problem


def api_error(*args, **kwargs) -> BackendError:
    return BackendError(api_failure(*args, **kwargs))


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
    operation: str | None = None,
    private: tuple[str, ...] = (),
    response_headers=(),
    elapsed_ms=None,
) -> BackendFailure:
    """Classify failures, projecting only safe gRPC text. Cancellation is never classified."""
    if isinstance(exc, asyncio.CancelledError):
        raise exc
    recorded_stage, outcome = evidence(stage, trailers=trailers and isinstance(exc, GRPCError))
    api = {"operation": operation or phase, "transport": "grpc", "elapsed_ms": elapsed_ms}
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
        return api_failure(
            ErrorCategory.CONNECTIVITY,
            tls,
            "TLS verification or handshake failed.",
            request_id,
            diagnostics=diagnostics(phase=phase, tls=tls),
            **api,
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
        response = api_error_data(message=exc.message, headers=response_headers, private=private)
        if exc.details is not None:
            # No arbitrary repr/serialization of protobuf Any or binary detail payloads.
            details = exc.details if isinstance(exc.details, (list, tuple)) else (exc.details,)
            response["detail_count"] = len(details)
            response["detail_types"] = ",".join(type(item).__name__ for item in details[:16])
            response["details_omitted"] = True
        return api_failure(
            category,
            code,
            "Server finished the call with an error status.",
            request_id,
            recorded_stage,
            outcome,
            terminal_status=status.name if trailers else None,
            timeout_phase=phase if category == ErrorCategory.TIMEOUT else None,
            diagnostics=diagnostics(phase=phase, status=status.name, trailers="received" if trailers else "none"),
            response=response,
            **api,
        )
    if isinstance(exc, TimeoutError):
        return api_failure(
            ErrorCategory.TIMEOUT,
            phase + "_deadline",
            "Backend operation deadline exceeded.",
            request_id,
            recorded_stage,
            outcome,
            timeout_phase=phase,
            diagnostics=diagnostics(phase=phase),
            **api,
        )
    if isinstance(exc, (StreamTerminatedError, ProtocolError, OSError, EOFError)):
        return api_failure(
            ErrorCategory.CONNECTIVITY,
            "connect_failed" if stage == SubmissionStage.NOT_SUBMITTED else "connection_lost",
            "Connection to the server failed or was lost.",
            request_id,
            recorded_stage,
            outcome,
            diagnostics=diagnostics(phase=phase, kind=type(exc).__name__),
            **api,
        )
    return api_failure(
        ErrorCategory.PROTOCOL,
        "unexpected_provider_failure",
        "Unexpected failure in the provider integration.",
        request_id,
        recorded_stage,
        outcome,
        diagnostics=diagnostics(phase=phase, kind=type(exc).__name__),
        **api,
    )
