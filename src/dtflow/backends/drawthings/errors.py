"""Failure construction for the Draw Things integration (sanitized: codes and status names only)."""

from __future__ import annotations

from dtflow.core.models import (
    BackendError,
    BackendFailure,
    ErrorCategory,
    Metadata,
    RemoteOutcome,
    SubmissionStage,
)


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
