"""Deterministic offline backend with explicit fake provenance."""

import asyncio
from datetime import UTC, datetime
from typing import Awaitable, Callable

from dtflow.core.models import (
    BackendError,
    BackendEvent,
    BackendFailure,
    Capabilities,
    CompletionEvidence,
    ConnectionStatus,
    Designation,
    ErrorCategory,
    EventSink,
    GenerationResult,
    ImageArtifact,
    InventoryEntry,
    InventorySnapshot,
    RemoteOutcome,
    ResolvedGenerationRequest,
    SubmissionStage,
    ValidationError,
)
from dtflow.core.png import fixture_png
from dtflow.core.tasks import settle


def now() -> str:
    return datetime.now(UTC).isoformat()


class FakeBackend:
    """Scenario-controlled fake; output is a swatch/grid, never model inference."""

    def __init__(
        self,
        *,
        scenario: str = "success",
        delay: float = 0,
        max_concurrency: int = 4,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        gate: asyncio.Event | None = None,
    ):
        if scenario not in ("success", "reject", "lost", "empty", "terminal_error"):
            raise ValidationError("Unsupported fake scenario.")
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 4:
            raise ValidationError("Fake concurrency must be in 1..4.")
        if type(delay) not in (int, float) or not 0 <= delay <= 600:
            raise ValidationError("Fake delay must be in 0..600 seconds.")
        self.scenario, self.delay, self.sleep, self.gate = scenario, delay, sleep, gate
        self._capabilities = Capabilities(
            "fake", model_ids=("fake-fixture", "fake-grid"), max_concurrency=max_concurrency, lora_support="selection"
        )
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._opened = False
        self.active = 0
        self.peak_active = 0

    @property
    def capabilities(self) -> Capabilities:
        return self._capabilities

    async def open(self) -> None:
        self._opened = True

    def _require_open(self) -> None:
        if not self._opened:
            raise BackendError(BackendFailure(ErrorCategory.CONNECTIVITY, "backend_closed", "Fake backend is closed."))

    async def check_connection(self) -> ConnectionStatus:
        self._require_open()
        return ConnectionStatus("fake", "fixture://fake", now(), "ok", "not_applicable", "ok", (("fake", "true"),))

    async def get_inventory(self, refresh: bool = False) -> InventorySnapshot:
        self._require_open()
        return InventorySnapshot(
            "fake",
            "fixture-v2",
            now(),
            refresh,
            (
                InventoryEntry(
                    "fake-fixture", "model", "Offline fixture swatch", "fixture", "present", "bundled-fixture", "1"
                ),
                InventoryEntry(
                    "fake-grid", "model", "Offline calibration grid", "fixture", "present", "bundled-fixture", "2"
                ),
                InventoryEntry(
                    "fake-soft",
                    "lora",
                    "Fixture matching architecture",
                    "fixture",
                    "present",
                    "bundled-fixture",
                    "2",
                    default_weight=0.8,
                    trigger_prefix="fixture-style",
                ),
                InventoryEntry(
                    "fake-mismatch",
                    "lora",
                    "Fixture mismatched architecture",
                    "other-fixture",
                    "present",
                    "bundled-fixture",
                    "2",
                ),
                InventoryEntry(
                    "fake-unknown", "lora", "Fixture unknown architecture", None, "present", "bundled-fixture", "2"
                ),
            ),
            (("fake", "true"),),
        )

    async def generate(self, request: ResolvedGenerationRequest, *, on_event: EventSink) -> GenerationResult:
        self._require_open()
        stage = SubmissionStage.NOT_SUBMITTED
        sequence = 0

        def emit(kind: str, **payload) -> None:
            nonlocal sequence
            sequence += 1
            try:
                on_event(BackendEvent(request.request_id, sequence, kind, now(), **payload))
            except Exception as exc:
                raise BackendError(
                    BackendFailure(
                        ErrorCategory.PROTOCOL,
                        "event_sink_failure",
                        "Event sink failed.",
                        request.request_id,
                        stage,
                        RemoteOutcome.NOT_STARTED if stage == SubmissionStage.NOT_SUBMITTED else RemoteOutcome.UNKNOWN,
                    )
                ) from exc

        def fail(code: str, category: ErrorCategory, outcome: RemoteOutcome) -> None:
            raise BackendError(
                BackendFailure(
                    category,
                    code,
                    f"Fake scenario: {code}.",
                    request.request_id,
                    stage,
                    outcome,
                    "ERROR" if stage == SubmissionStage.TERMINAL_OBSERVED else None,
                )
            )

        if (
            request.model_id not in self.capabilities.model_ids
            or any(item.identifier not in ("fake-soft", "fake-unknown") for item in request.loras)
            or request.prepared_assets
            or (request.batch_size, request.batch_count) != (1, 1)
        ):
            fail("unsupported_request", ErrorCategory.VALIDATION, RemoteOutcome.NOT_STARTED)
        emit("preparing", stage="fixture")
        if self.scenario == "reject":
            fail("pre_submission_rejection", ErrorCategory.REMOTE_REJECTION, RemoteOutcome.NOT_STARTED)
        async with self._semaphore:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            try:
                # Emitted conservatively before simulated submission; it is not server progress.
                stage = SubmissionStage.POSSIBLY_SUBMITTED
                emit("submission_possible", stage="fake")
                if self.gate is not None:
                    await self.gate.wait()
                await self.sleep(self.delay)
                if self.scenario == "lost":
                    fail("connection_lost", ErrorCategory.CONNECTIVITY, RemoteOutcome.UNKNOWN)
                stage = SubmissionStage.TERMINAL_OBSERVED
                if self.scenario == "terminal_error":
                    fail("terminal_error", ErrorCategory.REMOTE_REJECTION, RemoteOutcome.FAILED)
                emit("progress", stage="fixture_complete", completed=1, total=1)
                artifacts = ()
                if self.scenario != "empty":
                    encoded = await settle(
                        asyncio.to_thread(
                            fixture_png,
                            request.width_px,
                            request.height_px,
                            request.seed,
                            grid=request.model_id == "fake-grid",
                        )
                    )
                    artifact = ImageArtifact(
                        "fixture",
                        Designation.FINAL,
                        "image/png",
                        encoded,
                        request.width_px,
                        request.height_px,
                        (("fake", "true"),),
                    )
                    emit("artifact_received", artifact=artifact)
                    artifacts = (artifact,)
                emit("terminal", stage="OK")
                return GenerationResult(
                    request.request_id,
                    "fake",
                    "fixture-v1",
                    artifacts,
                    (
                        ("seed", str(request.seed)),
                        ("model_id", request.model_id),
                        ("steps", str(request.steps)),
                        ("guidance", str(request.guidance)),
                        ("batch_size", str(request.batch_size)),
                        ("batch_count", str(request.batch_count)),
                        ("width_px", str(request.width_px)),
                        ("height_px", str(request.height_px)),
                        ("sampler_id", request.sampler_id.value),
                        ("fake", "true"),
                    ),
                    CompletionEvidence("OK", bool(artifacts)),
                    ("Fixture output; no model inference performed.",),
                )
            finally:
                self.active -= 1

    async def aclose(self) -> None:
        if self.active:
            raise RuntimeError("Cancel/wait for owned generations before closing the fake backend.")
        self._opened = False
