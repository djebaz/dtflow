"""One async application path from immutable request to durable output."""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
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
from dtflow.core import provider_phase
from dtflow.core.provider_phase import OVERLAP
from dtflow.core.storage import AttemptStore
from dtflow.core.tasks import settle
from dtflow.diagnostics import ATTEMPT
from dtflow.diagnostics import cloud_key_data, event as diagnostic_event, request_fingerprint, timing_enabled
from dtflow.persistence.original_io import (
    OriginalImagesBusyError,
    OriginalImagesUnavailableError,
    original_images_lock,
    require_originals_ready,
)
from dtflow.core.output_layout import OutputLayout
from dtflow.services.artifacts import save_artifacts, validate_result
from dtflow.services.video_artifacts import require_video_destination
from dtflow.services import video_encode
from dtflow.services.assets import AssetService, AssetValidator
from dtflow.services.budget import BufferBudget
from dtflow.services.catalogue import Catalogue
from dtflow.services.events import EventBuffer
from dtflow.services.image_artifacts import save_image_artifacts
from dtflow.services.image_inputs import snapshot_image_inputs
from dtflow.services.provider_admission import Permit, QueueAdmission
from dtflow.services.preflight import checkpoint_exact_version, preflight_loras, raise_if_blocked
from dtflow.services.timeline import GenerationTimeline

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


class SubmissionHeld(Exception):
    """The caller's check said no (its queue was paused or cancelled) and nothing was sent. ``attempt_id`` names the
    prepared attempt this call had already created and has ended as cancelled; None when no attempt exists yet."""

    def __init__(self, attempt_id: str | None = None):
        super().__init__(attempt_id)
        self.attempt_id = attempt_id


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
        admission: QueueAdmission | None = None,
    ):
        self.admission = admission
        self.assets = assets
        self.catalogue = (
            Catalogue(assets.state) if isinstance(assets, AssetService) and assets.state is not None else None
        )
        self.backend, self.store, self.output = backend, store, Path(output)
        self.timeouts = timeouts or TimeoutPolicy()
        self.saver = saver
        self.input_state = Path(input_state) if input_state is not None else None
        if type(preview_images) is not bool:
            raise ValidationError("Preview image policy must be a boolean.")
        self.preview_images = preview_images
        # With provider admission, calls past their sampling phase overlap the active ones; each holds its reservation.
        self.budget = budget or BufferBudget(
            backend.capabilities.limits.buffer_budget * (OVERLAP if admission is not None else 1)
        )
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

    def _key_diagnostics(self) -> dict:
        """Return the non-secret identity pinned to this backend, not the current UI selection."""
        return cloud_key_data(
            getattr(self.backend, "credential_usage", None),
            getattr(self.backend, "credential_source", None),
            alias=getattr(self.backend, "credential_alias", None),
        )

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
        snapshot = await self.get_inventory()
        return preflight_loras(
            request,
            snapshot,
            checkpoint_version=checkpoint_exact_version(request.model_id, snapshot),
        )

    async def generate(
        self,
        request: ResolvedGenerationRequest,
        *,
        on_event: EventSink | None = None,
        prepared_inputs: PreparedImageInputs | None = None,
        attempt_id: str | None = None,
        layout: OutputLayout | None = None,
        permit: Permit | None = None,
        may_submit: Callable[[], Awaitable[bool]] | None = None,
    ) -> SavedGeneration:
        """``may_submit`` is asked twice: after preparation and before the attempt exists, and again as close to the
        provider as the flow allows (after the buffer budget and image snapshot, just before the attempt is marked
        submitted). False raises :class:`SubmissionHeld`; nothing is sent, and a prepared attempt is ended as cancelled.

        ``permit`` is the provider slot the queue dispatcher reserved when it claimed the item; without one, a call
        under admission waits its turn for a slot. The call closes it (idempotent) however it ends.

        ``attempt_id`` lets a caller that records the attempt elsewhere first (script runs) name it up front.

        ``layout`` names the run folder and file-name label the outputs are saved under (issue #296).

        With ``-time`` every generation, whichever caller starts it (Generate, queues, scripts, workflows), also writes
        one INFO ``generation.attempt_timing`` record: first-seen offsets of the fixed stages since this call began,
        plus short digests telling whether the prompt and the other settings repeated. It only observes."""

        async def run(sink):
            held = permit
            try:
                if request.num_frames is not None:
                    from dtflow.services.video_resources import video_admission
                    from dtflow.services.video_queue import check_source, validate_video

                    validate_video(
                        request, "cloud" if self.backend.capabilities.backend_id == "drawthings-cloud" else "other"
                    )
                    if held is not None:
                        held.provider_done()  # waiting for local video resources must not hold a provider slot
                    if sink is not None:
                        sink(
                            BackendEvent(
                                request.request_id,
                                0,
                                "preparing",
                                datetime.now(UTC).isoformat(),
                                stage="waiting_video_resources",
                            )
                        )
                    async with video_admission(request, self.output) as lease:
                        try:
                            await settle(asyncio.to_thread(check_source, request))
                        except (OSError, ValueError) as exc:
                            raise BackendError(
                                BackendFailure(
                                    ErrorCategory.VALIDATION,
                                    "video_source_unavailable",
                                    "The selected video source is missing, changed or unreadable. Nothing was submitted.",
                                    request.request_id,
                                )
                            ) from exc
                        held = await self._take_slot(held, request, sink)
                        return await self._generate(
                            request, sink, prepared_inputs, attempt_id, layout, held, lease, may_submit
                        )
                held = await self._take_slot(held, request, sink)
                return await self._generate(request, sink, prepared_inputs, attempt_id, layout, held, None, may_submit)
            finally:
                if held is not None:
                    held.close()

        if not timing_enabled():
            return await run(on_event)
        timeline = GenerationTimeline(
            self.backend.capabilities.backend_id,
            on_event,
            name="generation.attempt_timing",
            extra=request_fingerprint(request),
        )
        try:
            saved = await run(timeline.sink)
        except BaseException as exc:
            timeline.finish(exc)
            raise
        timeline.finish(None)
        return saved

    async def _take_slot(
        self, permit: Permit | None, request: ResolvedGenerationRequest, on_event: EventSink | None
    ) -> Permit | None:
        """Hold a provider slot before anything durable exists; only a call that must wait says so."""
        if self.admission is None:
            return None

        def waiting() -> None:
            if on_event is not None:
                on_event(
                    BackendEvent(
                        request.request_id,
                        0,
                        "preparing",
                        datetime.now(UTC).isoformat(),
                        stage="waiting_provider_slot",
                    )
                )

        if permit is None:
            return await self.admission.acquire(waiting)
        await permit.reacquire(waiting)
        return permit

    async def _generate(
        self,
        request: ResolvedGenerationRequest,
        on_event: EventSink | None,
        prepared_inputs: PreparedImageInputs | None,
        attempt_id: str | None,
        layout: OutputLayout | None,
        permit: Permit | None = None,
        video_lease=None,
        may_submit: Callable[[], Awaitable[bool]] | None = None,
    ) -> SavedGeneration:
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
        elif prepared_inputs is not None and request.pose_control is None:
            raise ValueError("Text-to-image cannot include prepared image inputs.")
        if request.pose_control is not None:
            if caps.backend_id != "drawthings-cloud" or self.input_state is None or prepared_inputs is None:
                raise ValidationError("Pose requires a durable cloud session and prepared inputs.")
            if (
                ImageInputSnapshot.from_prepared(
                    request.image_inputs.attempt_id, prepared_inputs, preserve_outside=False
                )
                != request.image_inputs
            ):
                raise ValidationError("Pose inputs do not match the request.")
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
        if isinstance(self.assets, AssetService) and request.loras:
            request = await self.assets.resolve_selected(request)
        if request.prepared_assets:
            preparation("validating_assets")
            # A fresh DT+ metadata check validates each bound LoRA before the attempt exists.
            # Only a positive missing-file result may restore verified retained bytes; identical content may rebind
            # to a newer server reference. Unknown/authentication failures cannot authorize an upload.
            if self.assets is None or any(item.backend_id != caps.backend_id for item in request.prepared_assets):
                raise BackendError(
                    BackendFailure(
                        ErrorCategory.VALIDATION,
                        "prepared_asset_unsupported",
                        "Prepared cloud assets are unsupported by the selected backend.",
                        request.request_id,
                    )
                )
            # One local per-key receipt gate; first-use assets share a batched BYOM lookup.
            # No routine network validation once this key owns confirmed copies.
            ensure_many = getattr(self.assets, "ensure_many", None)
            if isinstance(self.assets, AssetService):

                def resolved_assets(report: dict) -> None:
                    # An actual restoration gets a named UI notice; routine reuse/matches remain quiet.
                    restored = report["restored_names"]
                    if restored and on_event is not None:
                        on_event(
                            BackendEvent(
                                request.request_id,
                                0,
                                "assets_restored",
                                datetime.now(UTC).isoformat(),
                                stage=", ".join(restored),
                            )
                        )
                    if report["error"] and on_event is not None:
                        on_event(
                            BackendEvent(
                                request.request_id,
                                0,
                                "assets_inconclusive" if report["outcome"] == "inconclusive" else "assets_failed",
                                datetime.now(UTC).isoformat(),
                                stage=report["error"],
                            )
                        )

                # A Queue/Workflow/Script owns its own status callback; interactive Generate uses this one.
                feedback = resolved_assets if on_event is not None and self.assets.on_resolution is None else None
                confirmed = await self.assets.ensure_many(request.prepared_assets, preparation, on_resolution=feedback)
            elif callable(ensure_many):
                confirmed = await ensure_many(request.prepared_assets, preparation)
            else:
                confirmed = [
                    await self.assets.ensure(prepared, lambda stage: preparation(stage))
                    for prepared in request.prepared_assets
                ]
            if tuple(confirmed) != tuple(request.prepared_assets):
                pairs = list(zip(request.prepared_assets, confirmed, strict=True))
                # A different hash means the name now resolves to other bytes than the request was built with (#361).
                swapped = [f"{old.sha256[:8]}>{new.sha256[:8]}" for old, new in pairs if old.sha256 != new.sha256]
                changed = [new.receipt_id for old, new in pairs if old != new and old.sha256 == new.sha256]
                evidence = [
                    pair for pair in request.resolution_evidence if not (swapped and pair[0] == "prepared_assets")
                ]
                if swapped:
                    evidence.append(("prepared_assets", ",".join(asset.receipt_id for asset in confirmed)))
                    evidence.append(("receipts_updated", ",".join(swapped)))
                    diagnostic_event(log, "assets.receipts_updated", level=logging.INFO, data={"count": len(swapped)})
                if changed:
                    evidence.append(("restored_assets", ",".join(receipt[:8] for receipt in changed)))
                request = replace(request, prepared_assets=tuple(confirmed), resolution_evidence=tuple(evidence))
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
        if request.num_frames is not None:
            # Real executable/encoder readiness runs on a worker, never in a Qt handler.
            tool_problem = await settle(asyncio.to_thread(video_encode.tools_readiness_problem))
            if tool_problem is not None:
                raise BackendError(
                    BackendFailure(
                        ErrorCategory.VALIDATION,
                        "ffmpeg_missing",
                        tool_problem,
                        request.request_id,
                    )
                )
            try:
                await settle(asyncio.to_thread(require_video_destination, self.output, layout))
            except (OSError, ValueError) as exc:
                diagnostic_event(
                    log,
                    "video.storage_unavailable",
                    level=logging.WARNING,
                    data={"request_id": request.request_id, "reason": type(exc).__name__},
                )
                raise BackendError(
                    BackendFailure(
                        ErrorCategory.PERSISTENCE,
                        "video_storage_unavailable",
                        "Video output storage is unavailable. Generation was not submitted.",
                        request.request_id,
                    )
                ) from exc
        if may_submit is not None and not await may_submit():
            raise SubmissionHeld
        reserved = False
        if attempt_id is None:
            attempt_id = uuid4().hex
        elif not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            raise ValueError("A caller-named attempt id must be 32 lowercase hex characters.")
        if image_operation or request.pose_control is not None:
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
                **self._key_diagnostics(),
            },
        )
        stage = SubmissionStage.NOT_SUBMITTED
        outcome = RemoteOutcome.NOT_STARTED
        state = "prepared"
        last_sequence = 0
        events = EventBuffer(preview_bytes=caps.limits.preview_bytes)
        backend_submitted_at: float | None = None
        backend_terminal_at: float | None = None
        cloud = caps.backend_id == "drawthings-cloud"

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
            if (
                permit is not None
                and cloud
                and event.kind == "progress"
                and event.stage == "sampling"
                and event.total
                and (event.completed or 0) >= event.total
            ):
                permit.provider_done()  # DT+ finished sampling this call; the next one may start while it downloads
            events(event)
            if on_event is not None:
                on_event(event)

        try:
            preparation("waiting_buffer_budget")
            await self.budget.acquire(reservation)
            reserved = True
            if image_operation or request.pose_control is not None:
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
            if request.num_frames is not None:
                if video_lease is not None:
                    await settle(asyncio.to_thread(video_lease.recheck))
                tool_problem = await settle(asyncio.to_thread(video_encode.tools_readiness_problem))
                if tool_problem is not None:
                    raise BackendError(
                        BackendFailure(
                            ErrorCategory.VALIDATION,
                            "ffmpeg_missing",
                            f"{tool_problem} No video was sent.",
                            request.request_id,
                        )
                    )
                try:
                    await settle(asyncio.to_thread(require_video_destination, self.output, layout))
                except (OSError, ValueError) as exc:
                    raise BackendError(
                        BackendFailure(
                            ErrorCategory.PERSISTENCE,
                            "video_storage_unavailable",
                            "Video output storage became unavailable before submission. No video was sent.",
                            request.request_id,
                        )
                    ) from exc
                diagnostic_event(
                    log,
                    "video.storage_ready",
                    level=logging.DEBUG,
                    data={
                        "request_id": request.request_id,
                        "revalidated": True,
                        "ffmpeg_ready": True,
                        "ffprobe_ready": True,
                    },
                )
            if may_submit is not None and not await may_submit():
                failure = BackendFailure(
                    ErrorCategory.CANCELLATION,
                    "held_before_submission",
                    "The queue was paused or cancelled before this was sent; nothing was sent.",
                    request.request_id,
                    SubmissionStage.NOT_SUBMITTED,
                    RemoteOutcome.NOT_STARTED,
                    cancellation=CancellationEvidence(True, True, False, False),
                )
                await self.store.transition(
                    attempt_id,
                    ("prepared",),
                    "cancelled",
                    SubmissionStage.NOT_SUBMITTED,
                    RemoteOutcome.NOT_STARTED,
                    failure=failure,
                )
                state = "cancelled"
                raise SubmissionHeld(attempt_id)
            preparation("recording_submission_state")
            await self.store.transition(
                attempt_id,
                ("prepared",),
                "possibly_submitted",
                SubmissionStage.POSSIBLY_SUBMITTED,
                RemoteOutcome.UNKNOWN,
            )
            state, stage, outcome = "possibly_submitted", SubmissionStage.POSSIBLY_SUBMITTED, RemoteOutcome.UNKNOWN
            # A video's deadline covers remote receiving only and is enforced inside the video path; a downloaded
            # clip waiting for a local encoder (cancellable) must not expire here (#550).
            total_timeout = None if request.num_frames is not None else self.timeouts.total
            async with asyncio.timeout(total_timeout):
                with provider_phase.bound(permit.provider_done if permit is not None else None):
                    result = await self.backend.generate(request, on_event=receive)
                if permit is not None:
                    permit.provider_done()
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

                try:
                    saved = await settle(asyncio.to_thread(commit_originals))
                except Exception as exc:
                    if request.num_frames is not None:
                        diagnostic_event(
                            log,
                            "video.artifacts_save_failed",
                            level=logging.WARNING,
                            data={"attempt_id": attempt_id, "reason": type(exc).__name__},
                        )
                    raise
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
                    **self._key_diagnostics(),
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
            if request.num_frames is not None:
                diagnostic_event(
                    log,
                    "video.attempt_interrupted",
                    level=logging.WARNING,
                    data={"attempt_id": attempt_id, "submission_stage": stage, "remote_outcome": outcome},
                )
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
        except SubmissionHeld:
            raise
        except BackendError as exc:
            failure = exc.failure
            if self.assets is not None and request.prepared_assets:
                # A failure may mean a LoRA changed on DT+: the next item must ask again instead of trusting an old answer.
                self.assets.forget(request.prepared_assets)
            diagnostic_event(
                log,
                "generation.attempt_failed",
                level=logging.WARNING if dict(failure.diagnostics).get("transport") else logging.ERROR,
                data={
                    "code": failure.code,
                    "category": failure.category,
                    "outcome": failure.remote_outcome,
                    **self._key_diagnostics(),
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
