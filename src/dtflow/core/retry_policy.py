"""The one automatic-retry rule shared by Generate, Queues, Scripts and Workflows (#220, #241).

A submission is retried automatically at most ``auto_retry_limit`` times (``AUTO_RETRIES``, plus one more for the
LoRA case below), after ``AUTO_RETRY_DELAY`` seconds, and only when
the first try definitely produced nothing:

* a definite failure (nothing was started, or the backend reported the failure) of category connectivity, timeout or
  protocol; or
* the Draw Things+ case where the server itself ended the call with gRPC ``INTERNAL`` "unknown error processing request"
  (terminal status received, no image). Owner decision on #241: these come back within seconds, are not tied to the
  seed or the request, and cluster on the first submissions after Start.

A request that carries custom LoRAs gets one extra try for that DT+ INTERNAL case, in every runner (#361); the LoRA
check and restore runs between the tries. In Workflows and Scripts the extra try is also bounded by the run's own
allowance and generation limit.

Never retried automatically: any other unknown outcome (lost connection, timeout after sending, no terminal status),
credentials/authentication/permission, quota/resource limit, validation, remote rejections, a result that was generated
but not saved, and a user stop. Those keep the manual Retry. Each runner adds ``AUTO_RETRIES`` per planned submission to
its existing loop bound, so a run stays within its planned submissions x 2, apart from the extra LoRA try above.
No other classifier exists.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from dtflow.core.models import (
    BackendFailure,
    ErrorCategory,
    RemoteOutcome,
    ResolvedGenerationRequest,
)

#: Automatic retries per submission.
AUTO_RETRIES = 1
#: Seconds to wait before an automatic retry.
AUTO_RETRY_DELAY = 5.0
#: Request evidence naming the request an automatic retry replaces (a manual Retry uses ``retry.of_request``).
AUTO_RETRY_EVIDENCE = "retry.automatic_of_request"
#: User-facing stage text while waiting to retry.
RETRY_STAGE = "Retrying automatically"

_TRANSIENT = frozenset({ErrorCategory.CONNECTIVITY, ErrorCategory.TIMEOUT, ErrorCategory.PROTOCOL})
_DEFINITE = frozenset({RemoteOutcome.NOT_STARTED, RemoteOutcome.FAILED})
_SERVER_NO_RESULT = "unknown error processing request"


def server_ended_without_result(failure: BackendFailure) -> bool:
    """DT+ ended the call itself with gRPC INTERNAL "unknown error processing request" and sent no image.

    A gRPC status is the server's own reply, so the code and its message are the evidence; trailers are not required
    (a trailers-only reply carries the status in its headers)."""
    return (
        failure.code == "grpc_internal"
        and failure.remote_outcome != RemoteOutcome.SUCCEEDED
        and not failure.partial_artifacts
        and _SERVER_NO_RESULT in (failure.message or "").lower()
    )


def auto_retryable(failure: BackendFailure) -> bool:
    """Whether this failure may get the one automatic retry (see the module rules)."""
    if server_ended_without_result(failure):
        return True
    return failure.category in _TRANSIENT and failure.remote_outcome in _DEFINITE


def auto_retry_limit(request: ResolvedGenerationRequest, failure: BackendFailure) -> int:
    """Automatic retries for this failure: one more when DT+ ended a request that carries custom LoRAs without a result
    (#361). The LoRA check and restore (``AssetService.recover_missing``) runs between the tries, and one try is not
    always enough while DT+ starts serving a LoRA it has just been told about."""
    if request.prepared_assets and server_ended_without_result(failure):
        return AUTO_RETRIES + 1
    return AUTO_RETRIES


def auto_retry_request(request: ResolvedGenerationRequest) -> ResolvedGenerationRequest:
    """The same request (same settings and seed) under a new request identity that names the try it replaces."""
    return replace(
        request,
        request_id=uuid4().hex,
        resolution_evidence=(
            *(pair for pair in request.resolution_evidence if pair[0] != AUTO_RETRY_EVIDENCE),
            (AUTO_RETRY_EVIDENCE, request.request_id),
        ),
    )
