"""One async application path from immutable request to durable output."""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

from dtflow.build import identity
from dtflow.core.image_inputs import INPUT_NAMES, ImageInputSnapshot, PreparedImageInputs
from dtflow.core.models import (
    Backend,
    BackendError,
    BackendEvent,
    BackendFailure,
    CancellationEvidence,
    ErrorCategory,
    EventSink,
    RemoteOutcome,
    ResolvedGenerationRequest,
    SavedGeneration,
    SubmissionStage,
    TimeoutPolicy,
    ValidationError,
)
from dtflow.core.storage import AttemptStore
from dtflow.core.tasks import settle
from dtflow.diagnostics import ATTEMPT
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.original_io import (
    OriginalImagesBusyError,
    OriginalImagesUnavailableError,
    original_images_lock,
    require_originals_ready,
)
from dtflow.core.output_layout import OutputLayout
from dtflow.services.artifacts import save_artifacts, validate_result
from dtflow.services.assets import AssetValidator
from dtflow.services.budget import BufferBudget
from dtflow.services.events import EventBuffer
from dtflow.services.image_artifacts import save_image_artifacts
from dtflow.services.image_inputs import snapshot_image_inputs
from dtflow.services.preflight import preflight_loras, raise_if_blocked

log = logging.getLogger(__name__)


def _buffer_reservation(request: ResolvedGenerationRequest, caps, prepared_inputs: PreparedImageInputs | None) -> int:
    """Reserve one cloud text-to-image artifact; retain conservative image-edit buffers."""
    raw_bytes = request.height_px * (1 + request.width_px * 4)
    if caps.backend_id == "drawthings-cloud" and prepared_inputs is None:
        # Cloud inference is remote and permits one output per request; do not reserve three artifacts.
        return caps.limits.artifact_bytes
    reservation = raw_bytes + 3 * caps.limits.artifact_bytes + caps.limits.preview_bytes
    if prepared_inputs is not None:
        # Edits retain prepared grids, encoded PNGs and SDK tensors; keep their larger allowance.
        reservation += (
            sum(
                len(getattr(prepared_inputs, name).encoded)
                for name in INPUT_NAMES
                if getattr(prepared_inputs, name) is not None
            )
            + prepared_inputs.source.width * prepared_inputs.source.height * 16
            + request.width_px * request.height_px * 24
        )
    return reservation


class GenerationService:
    """Own orchestration only; adapters, persistence and presentation are injected."""

    def __init__(
        self,
        backend: Backend,
        store: AttemptStore,
        output: Path,
        *,
        timeouts: TimeoutPolicy | None = None,
        saver: Callable = save_artifacts,
        budget: BufferBudget | None = None,
        assets: AssetValidator | None = None,
        input_state: Path | None = None,
        preview_images: bool = True,
    ):
        self.assets = assets
        self.backend, self.store, self.output = backend, store, Path(output)
        self.timeouts = timeouts or TimeoutPolicy()
        self.saver = saver
        self.input_state = Path(input_state) if input_state is not None else None
        if type(preview_images) is not bool:
            raise ValidationError("Preview image policy must be a boolean.")
        self.preview_images = preview_images
        self.budget = budget or BufferBudget(backend.capabilities.limits.buffer_budget)
        # Serialize validation/saving while admitted backend requests may overlap.
        self._buffers = asyncio.Semaphore(1)

    async def _bounded(self, operation, deadline: float, phase: str):
        try:
            async with asyncio.timeout(deadline):
                return await operation
        except TimeoutError as exc:
            raise BackendError(
                BackendFailure(
                    ErrorCategory.TIMEOUT,
                    phase + "_deadline",
                    "Backend operation deadline exceeded.",
                    timeout_phase=phase,
                )
            ) from exc

    async def _attribute_credential(self, attempt_id: str) -> None:
        """Record which opaque key identity produced this saved output (cloud only); best effort, never a failure.

        The identity is a digest plus a short hint, never the credential. Missing attribution is disclosed by the
        usage display as unattributed rather than guessed.
        """
        usage = getattr(self.backend, "credential_usage", None)
        record = getattr(self.store, "record_credential", None)
        if usage is None or record is None:
            return
        try:
            await record(attempt_id, usage[0], usage[1])
        except Exception as exc:
            log.warning("Cloud key attribution could not be recorded: %s", type(exc).__name__)

    async def check_connection(self):
        return await self._bounded(
            self.backend.check_connection(), self.timeouts.connect + self.timeouts.inventory, "connectivity"
        )

    async def get_inventory(self, refresh: bool = False):
        return await self._bounded(self.backend.get_inventory(refresh=refresh), self.timeouts.inventory, "inventory")

    async def preflight(self, request: ResolvedGenerationRequest):
        """Structured LoRA diagnostics from the backend's current inventory; never dispatches anything."""
        return preflight_loras(request, await self.get_inventory())

    async def generate(
        self,
        request: ResolvedGenerationRequest,
        *,
        on_event: EventSink | None = None,
        prepared_inputs: PreparedImageInputs | None = None,
        attempt_id: str | None = None,
        layout: OutputLayout | None = None,
    ) -> SavedGeneration:
        """``attempt_id`` lets a caller that records the attempt elsewhere first (script runs) name it up front.

        ``layout`` names the run folder and file-name label the outputs are saved under (issue #296)."""

        def preparation(stage: str) -> None:
            if on_event is not None:
                on_event(BackendEvent(request.request_id, 0, "preparing", datetime.now(UTC).isoformat(), stage=stage))

        preparation("validating_request")
        caps = self.backend.capabilities
        image_operation = request.operation != "txt2img"
        if image_operation:
            gate = getattr(self.backend, "validate_image_operation", None)
            if not callable(gate) or prepared_inputs is None or self.input_state is None:
                raise BackendError(
                    BackendFailure(
                        ErrorCategory.VALIDATION,
                        "image_dispatch_not_configured",
                        "Image operations require a scoped owner gate and prepared managed inputs.",
                        request.request_id,
                    )
                )
            gate(request)
            expected = ImageInputSnapshot.from_prepared(
                request.image_inputs.attempt_id, prepared_inputs, preserve_outside=request.image_inputs.preserve_outside
            )
            if expected != request.image_inputs:
                raise ValueError("Prepared image inputs do not match the request.")
        elif prepared_inputs is not None:
            raise ValueError("Text-to-image cannot include prepared image inputs.")
        unsupported = tuple(
            (name, code)
            for name, code, failed in (
                ("operation", "not_offered", request.operation not in caps.operations),
                ("sampler", "not_offered", request.sampler_id not in caps.samplers),
                (
                    "model",
                    "not_offered",
                    not (caps.unlisted_model_bridge and request.allow_unlisted_model)
                    and request.model_id not in caps.model_ids,
                ),
                ("lora", "not_supported", bool(request.loras) and caps.lora_support == "unsupported"),
                (
                    "batch",
                    "unsupported",
                    (request.batch_size, request.batch_count) != (caps.batch_size, caps.batch_count),
                ),
            )
            if failed
        )
        if unsupported:
            raise BackendError(
                BackendFailure(
                    ErrorCategory.VALIDATION,
                    "unsupported_request",
                    "Request is unsupported by the selected backend.",
                    request.request_id,
                    diagnostics=unsupported,
                )
            )
        if request.prepared_assets:
            preparation("validating_assets")
            # Validation is metadata-only: a stale, inaccessible or unconfirmed reference blocks the request here,
            # before any attempt exists, and never triggers an upload or substitutes another receipt.
            if self.assets is None or any(item.backend_id != caps.backend_id for item in request.prepared_assets):
                raise BackendError(
                    BackendFailure(
                        ErrorCategory.VALIDATION,
                        "prepared_asset_unsupported",
                        "Prepared cloud assets are unsupported by the selected backend.",
                        request.request_id,
                    )
                )
            for prepared in request.prepared_assets:
                await self._bounded(self.assets.validate(prepared), self.timeouts.http, "asset_validation")
        if request.loras:
            preparation("checking_loras")
            # Confirmed incompatibility or absence must stop the request before any durable attempt exists.
            raise_if_blocked(request, await self.preflight(request))
        raw_bytes = request.height_px * (1 + request.width_px * 4)
        reservation = _buffer_reservation(request, caps, prepared_inputs)
        if raw_bytes > caps.limits.tensor_bytes or reservation > self.budget.limit:
            raise BackendError(
                BackendFailure(
                    ErrorCategory.RESOURCE_LIMIT,
                    "buffer_admission",
                    "Request exceeds the buffer admission budget.",
                    request.request_id,
                )
            )
        # One authoritative gate covers desktop, CLI, queues and image operations.
        # No child process, lock or Windows handshake is performed before dispatch.
        preparation("checking_output_storage")
        try:
            await asyncio.to_thread(require_originals_ready, getattr(self.store, "directory", self.output.parent))
        except OriginalImagesUnavailableError as exc:
            raise BackendError(
                BackendFailure(
                    ErrorCategory.PERSISTENCE,
                    "originals_unavailable",
                    f"{exc} Generation was not submitted; no provider generation was billed for this request.",
                    request.request_id,
                )
            ) from exc
        reserved = False
        if attempt_id is None:
            attempt_id = uuid4().hex
        elif not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            raise ValueError("A caller-named attempt id must be 32 lowercase hex characters.")
        if image_operation:
            request = replace(request, image_inputs=replace(request.image_inputs, attempt_id=attempt_id))
        ATTEMPT.set(attempt_id)
        preparation("creating_attempt")
        await self.store.create(attempt_id, caps.backend_id, request)
        diagnostic_event(
            log,
            "generation.attempt_prepared",
            level=logging.INFO,
            data={
                "backend": caps.backend_id,
                "request_id": request.request_id,
                "model_id": request.model_id,
            },
        )
        stage = SubmissionStage.NOT_SUBMITTED
        outcome = RemoteOutcome.NOT_STARTED
        state = "prepared"
        last_sequence = 0
        events = EventBuffer(preview_bytes=caps.limits.preview_bytes)
        backend_submitted_at: float | None = None
        backend_terminal_at: float | None = None

        def receive(event: BackendEvent) -> None:
            nonlocal last_sequence, backend_submitted_at, backend_terminal_at
            observed_at = monotonic()
            if event.request_id != request.request_id or event.sequence <= last_sequence:
                raise ValueError("Backend event identity/order is invalid.")
            last_sequence = event.sequence
            if event.kind == "preview" and not self.preview_images:
                return  # no unused image retention or forwarding, including injected/non-Draw-Things adapters
            if event.artifact and len(event.artifact.encoded) > caps.limits.artifact_bytes:
                raise ValueError("Event artifact exceeds the limit.")
            if event.kind == "submission_possible" and backend_submitted_at is None:
                backend_submitted_at = observed_at
            elif event.kind == "terminal":
                backend_terminal_at = observed_at
            events(event)
            if on_event is not None:
                on_event(event)

        try:
            preparation("waiting_buffer_budget")
            await self.budget.acquire(reservation)
            reserved = True
            if image_operation:
                preparation("saving_image_snapshot")
                snapshot = await settle(
                    asyncio.to_thread(
                        snapshot_image_inputs,
                        self.input_state,
                        attempt_id,
                        prepared_inputs,
                        preserve_outside=request.image_inputs.preserve_outside,
                    )
                )
                if snapshot != request.image_inputs:
                    raise ValueError("Committed image inputs differ from the prepared attempt.")
            preparation("recording_submission_state")
            await self.store.transition(
                attempt_id,
                ("prepared",),
                "possibly_submitted",
                SubmissionStage.POSSIBLY_SUBMITTED,
                RemoteOutcome.UNKNOWN,
            )
            state, stage, outcome = "possibly_submitted", SubmissionStage.POSSIBLY_SUBMITTED, RemoteOutcome.UNKNOWN
            async with asyncio.timeout(self.timeouts.total):
                result = await self.backend.generate(request, on_event=receive)
            stage = SubmissionStage.TERMINAL_OBSERVED
            if result.backend_id != caps.backend_id:
                raise ValueError("Result backend identity does not match.")
            if (
                backend_submitted_at is not None
                and backend_terminal_at is not None
                and backend_terminal_at >= backend_submitted_at
            ):
                result = replace(
                    result,
                    backend_elapsed_seconds=backend_terminal_at - backend_submitted_at,
                )
            # Validation/encoding run in the bounded worker path, not the owning loop.
            async with self._buffers:
                await settle(asyncio.to_thread(validate_result, result, request, caps.limits))
                outcome = RemoteOutcome.SUCCEEDED
                await self.store.transition(attempt_id, ("possibly_submitted",), "saving", stage, outcome)
                state = "saving"

                def commit_originals():
                    # Keep committed originals behind the same OS-released lease
                    # used by export and Recycle Bin; no provider code runs here.
                    # A paid result waits longer than ordinary leases before save_failed.
                    with original_images_lock(getattr(self.store, "directory", self.output.parent), timeout=30):
                        extra = {"layout": layout} if layout is not None else {}
                        if image_operation:
                            return save_image_artifacts(
                                self.output,
                                attempt_id,
                                request,
                                result,
                                identity(),
                                caps.limits,
                                prepared_inputs,
                                **extra,
                            )
                        return self.saver(self.output, attempt_id, request, result, identity(), caps.limits, **extra)

                saved = await settle(asyncio.to_thread(commit_originals))
                paths, metadata = saved[:2]
                display_image = saved[2] if image_operation else None
            await self._attribute_credential(attempt_id)
            await self.store.transition(
                attempt_id, ("saving",), "succeeded", stage, outcome, artifacts=(*paths, metadata)
            )
            diagnostic_event(
                log,
                "generation.attempt_succeeded",
                level=logging.INFO,
                data={
                    "outputs": len(paths),
                },
            )
            return SavedGeneration(
                attempt_id,
                paths,
                metadata,
                result,
                request=request if image_operation else None,
                display_image=display_image,
            )
        except asyncio.CancelledError:
            diagnostic_event(
                log,
                "generation.attempt_interrupted",
                level=logging.WARNING,
                data={
                    "stage": stage,
                    "outcome": outcome,
                },
            )
            failure = BackendFailure(
                ErrorCategory.CANCELLATION,
                "local_cancelled",
                "Local waiting stopped; no remote cancellation is confirmed.",
                request.request_id,
                stage,
                outcome,
                cancellation=CancellationEvidence(True, True, False, outcome == RemoteOutcome.UNKNOWN),
            )
            # A cancelled disk commit may have completed; inspect before the final transition.
            current = (await self.store.inspect(attempt_id))[0]
            if current["state"] not in ("succeeded", "failed", "cancelled", "unknown", "save_failed"):
                recorded_stage = SubmissionStage(current["submission_stage"])
                recorded_outcome = RemoteOutcome(current["remote_outcome"])
                failure = BackendFailure(
                    failure.category,
                    failure.code,
                    failure.message,
                    request.request_id,
                    recorded_stage,
                    recorded_outcome,
                    cancellation=CancellationEvidence(True, True, False, recorded_outcome == RemoteOutcome.UNKNOWN),
                )
                await self.store.transition(
                    attempt_id,
                    (current["state"],),
                    "unknown"
                    if recorded_outcome == RemoteOutcome.UNKNOWN
                    else ("save_failed" if recorded_outcome == RemoteOutcome.SUCCEEDED else "cancelled"),
                    recorded_stage,
                    recorded_outcome,
                    failure=failure,
                )
            raise
        except BackendError as exc:
            failure = exc.failure
            diagnostic_event(
                log,
                "generation.attempt_failed",
                level=logging.WARNING if dict(failure.diagnostics).get("transport") else logging.ERROR,
                data={
                    "code": failure.code,
                    "category": failure.category,
                    "outcome": failure.remote_outcome,
                },
            )
            await self.store.transition(
                attempt_id,
                (state,),
                "unknown" if failure.remote_outcome == RemoteOutcome.UNKNOWN else "failed",
                failure.submission_stage,
                failure.remote_outcome,
                failure=failure,
            )
            raise
        except TimeoutError as exc:
            failure = BackendFailure(
                ErrorCategory.TIMEOUT,
                "generation_deadline",
                "Generation deadline exceeded.",
                request.request_id,
                stage,
                outcome,
                timeout_phase="generation_total",
            )
            await self.store.transition(
                attempt_id,
                (state,),
                "save_failed" if outcome == RemoteOutcome.SUCCEEDED else "unknown",
                stage,
                outcome,
                failure=failure,
            )
            raise BackendError(failure) from exc
        except Exception as exc:
            local_save_failure = (
                state == "saving"
                and outcome == RemoteOutcome.SUCCEEDED
                and isinstance(exc, (OriginalImagesUnavailableError, OriginalImagesBusyError, OSError))
            )
            category = ErrorCategory.PERSISTENCE if state in ("saving", "prepared") else ErrorCategory.PROTOCOL
            failure = BackendFailure(
                category,
                "local_save_unavailable" if local_save_failure else "output_or_protocol_failure",
                (
                    "The image was generated but could not be saved locally; it may not have been saved "
                    "or may be incomplete, and generating again may be billed."
                    if local_save_failure
                    else "Output could not be validated or durably saved."
                ),
                request.request_id,
                stage,
                outcome,
            )
            # If the state store itself failed, leave the last durable marker for restart.
            current = (await self.store.inspect(attempt_id))[0]
            if current["state"] in ("prepared", "possibly_submitted", "saving"):
                await self.store.transition(
                    attempt_id,
                    (current["state"],),
                    "save_failed"
                    if outcome == RemoteOutcome.SUCCEEDED
                    else ("unknown" if outcome == RemoteOutcome.UNKNOWN else "failed"),
                    stage,
                    outcome,
                    failure=failure,
                )
            raise BackendError(failure) from exc

        finally:
            if reserved:
                await settle(self.budget.release(reservation))
