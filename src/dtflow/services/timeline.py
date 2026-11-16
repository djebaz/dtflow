"""First-seen timings of the fixed stages of one generation; one record when it settles."""

import asyncio
import logging
import re
from time import monotonic

from dtflow.core.models import BackendError, BackendEvent, EventSink
from dtflow.diagnostics import event as diagnostic_event

log = logging.getLogger(__name__)

# Fixed, privacy-safe stage names a generation timeline may record. Anything else (for example LoRA names carried by
# asset events) is ignored, so the record never holds names, paths, prompts or credentials.
TIMELINE_STAGES = frozenset(
    {
        "binding_assets",
        "opening_session",
        "backend_created",
        "store_opened",
        "backend_opened",
        "session_ready",
        "inventory_refreshed",
        "preflight_done",
        "validating_image_inputs",
        "validating_request",
        "validating_assets",
        "restoring_assets",
        "checking_loras",
        "checking_output_storage",
        "creating_attempt",
        "waiting_buffer_budget",
        "saving_image_snapshot",
        "recording_submission_state",
        "resolving_model",
        "preparing_image_inputs",
        "resolving_credentials",
        "encoding_request",
        "wire_validated",
        "waiting_execution_lane",
        "connecting_backend",
        "preparing_grpc_metadata",
        "waiting_backend_response",
        "backend_response_received",
        "retrying",
    }
)
_PROGRESS_STAGE = re.compile(r"[a-z_]{1,40}")


class GenerationTimeline:
    """When each fixed stage of one generation was first seen, in milliseconds since Generate was requested.

    It wraps the caller's event sink (``sink``), adds marks from the session functions, and writes one INFO
    ``generation.timeline`` record when the generation settles. It only observes: events are forwarded unchanged.
    """

    def __init__(
        self, backend: str, inner: EventSink | None, *, name: str = "generation.timeline", extra: dict | None = None
    ):
        self.backend = backend
        self.inner = inner
        self.name = name
        self.extra = extra or {}
        self.started = monotonic()
        self.marks: dict[str, int] = {}

    def mark(self, name: str) -> None:
        if name in TIMELINE_STAGES or name in _FIXED_KINDS or name.startswith("progress_"):
            self.marks.setdefault(name, round((monotonic() - self.started) * 1000))

    def sink(self, event: BackendEvent) -> None:
        if event.kind == "preparing":
            if event.stage in TIMELINE_STAGES:
                self.mark(event.stage)
        elif event.kind in _FIXED_KINDS:
            self.mark(event.kind)
        elif event.kind == "progress" and event.stage and _PROGRESS_STAGE.fullmatch(event.stage):
            self.mark("progress_" + event.stage)
        if self.inner is not None:
            self.inner(event)

    def finish(self, failure: BaseException | None) -> None:
        """One record per generation; ``outcome`` is saved, failed or cancelled, with the safe failure code if any."""
        data: dict = {
            "backend": self.backend,
            "total_ms": round((monotonic() - self.started) * 1000),
            **self.extra,
        }
        if failure is None:
            data["outcome"] = "saved"
        elif isinstance(failure, asyncio.CancelledError):
            data["outcome"] = "cancelled"
        else:
            data["outcome"] = "failed"
            if isinstance(failure, BackendError):
                data["code"] = failure.failure.code
        data.update({name + "_ms": value for name, value in self.marks.items()})
        diagnostic_event(log, self.name, level=logging.INFO, data=data)


_FIXED_KINDS = frozenset({"submission_possible", "preview", "artifact_received", "terminal"})
