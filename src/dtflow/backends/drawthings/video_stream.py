"""One video generation on Draw Things: send, receive N one-frame tensors plus an optional audio tensor, encode an MP4.

The image path (``DrawThingsBackend.generate``) keeps its own limits; a clip is far bigger (86 MiB at 384x640) and
takes minutes (up to 243 s seen), so this path has its own total deadline and no transport cap. Frames go to ffmpeg as
they arrive. The result carries the poster (first frame, PNG) as the only final artifact, plus the temporary MP4 path
and the probed facts in the artifact metadata; the artifact saver (``services.video_artifacts``) moves the MP4 beside
the poster.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import betterproto
from drawthings_py.generated.dt_grpc import image_service
from grpclib.const import Cardinality

from dtflow.backends.drawthings.errors import error, from_exception
from dtflow.backends.drawthings.versions import bundled_base_version
from dtflow.backends.drawthings.wire import build_generation_message
from dtflow.core.models import (
    BackendError,
    BackendEvent,
    CompletionEvidence,
    Designation,
    ErrorCategory,
    GenerationResult,
    ImageArtifact,
    ResolvedGenerationRequest,
    SubmissionStage,
)
from dtflow.core.png import encode_png
from dtflow.core.tasks import settle
from dtflow.core.video import START_IMAGE_EVIDENCE, VIDEO_TOTAL_TIMEOUT, audio_rate, playback_fps, video_profile
from dtflow.diagnostics import event as diagnostic_event
from dtflow.services import video_encode

log = logging.getLogger(__name__)
GENERATE_METHOD = "/ImageGenerationService/GenerateImage"


def _frame_rgb(tensor: bytes) -> tuple[int, int, bytes]:
    """One received frame tensor to packed RGB bytes (worker thread)."""
    import numpy as np
    from drawthings_py.image.image_buffer import ImageBuffer

    buffer = ImageBuffer.from_tensor(tensor)
    pixels = np.frombuffer(buffer.data, dtype=np.uint8).reshape(buffer.height, buffer.width, buffer.channels)
    return buffer.width, buffer.height, np.ascontiguousarray(pixels[:, :, :3]).tobytes()


def _audio_raw(data: bytes, target: Path) -> int:
    """Write the audio tensor as raw float32 little-endian stereo and return its sample count (worker thread)."""
    import numpy as np
    from drawthings_py.grpc.audio import AudioBuffer

    buffer = AudioBuffer.from_tensor(data)
    target.write_bytes(np.ascontiguousarray(buffer.data, dtype="<f4").tobytes())
    return int(buffer.length)


async def generate_video(backend, request: ResolvedGenerationRequest, on_event) -> GenerationResult:
    tools = video_encode.find_tools()
    if tools is None:
        raise error(ErrorCategory.VALIDATION, "ffmpeg_missing", video_encode.tools_problem(), request.request_id)
    ffmpeg, ffprobe = tools
    if request.start_image is not None:
        # The common img2img permit establishes exact backend, endpoint, checkpoint and
        # bundled-description identity; the video path still uses the txt2img wire.
        permit = backend._image_permits.get((request.model_id, "img2img"))
        if (
            permit is None
            or permit.evidence_id != START_IMAGE_EVIDENCE.get(request.model_id)
            or permit.status != "owner_verified"
        ):
            raise error(
                ErrorCategory.VALIDATION, "video_start_permit_missing",
                "The selected checkpoint has no exact owner-verified start-image permit.",
                request.request_id,
            )
        try:
            permit.validate_scope(
                backend.backend_id, backend.endpoint.label, request.model_id, "img2img",
            )
        except ValueError:
            raise error(
                ErrorCategory.VALIDATION, "video_start_permit_mismatch",
                "The start-image permit does not match this provider and checkpoint.",
                request.request_id,
            ) from None
    if backend._closing or backend._closed:
        raise error(ErrorCategory.CONNECTIVITY, "backend_closed", "Backend is closed.", request.request_id)
    if (
        not (backend.unlisted_model_bridge and request.allow_unlisted_model)
        and request.model_id not in backend.capabilities.model_ids
    ):
        raise error(
            ErrorCategory.VALIDATION,
            "unknown_model",
            "Model is not in the server inventory; refresh the inventory first.",
            request.request_id,
        )
    started = time.monotonic()
    deadline = max(backend.timeouts.total, VIDEO_TOTAL_TIMEOUT)
    sequence = 0
    stage = SubmissionStage.NOT_SUBMITTED
    metadata: dict = {}
    stream = None
    work = Path(tempfile.mkdtemp(prefix="dtflow-video-"))
    sink = video_encode.FrameSink(ffmpeg, playback_fps(request.model_id), work / "video.mp4.partial")
    frames = 0
    received = 0
    audio = bytearray()
    poster: bytes | None = None
    keep = False
    messages = 0
    declared_download_size: int | None = None
    last_receive_log = started
    first_response_ms: float | None = None
    first_content_ms: float | None = None
    last_stage = "preparing"
    audio_samples = 0
    inferred_audio_rate: int | None = None
    selected_audio_rate: int | None = None
    warnings: list[str] = []

    def trace(name: str, *, level: int = logging.DEBUG, **data) -> None:
        diagnostic_event(
            log, f"video.{name}", level=level,
            data={"request_id": request.request_id, **data},
        )

    trace(
        "pre_submit", model_id=request.model_id, requested_frames=request.num_frames,
        requested_width=request.width_px, requested_height=request.height_px,
        playback_fps=playback_fps(request.model_id), audio_rate_hz=audio_rate(request.model_id),
        profile_available=video_profile(request.model_id) is not None,
        start_image_present=request.start_image is not None,
        start_image_permit="owner_verified" if request.start_image else "not_required",
        ffmpeg_available=True, ffprobe_available=True, storage_ready=True,
    )

    def emit(kind: str, **payload) -> None:
        nonlocal sequence
        sequence += 1
        on_event(BackendEvent(request.request_id, sequence, kind, _now(), **payload))

    backend._active += 1
    try:
        async with asyncio.timeout(deadline):
            emit("preparing", stage="resolving_model")
            spec = await backend._model_spec(request.model_id)
            model = (
                next((e for e in backend._inventory.entries if e.identifier == request.model_id), None)
                if backend._inventory
                else None
            )
            base_version = (model.architecture if model else None) or bundled_base_version(request.model_id)
            emit("preparing", stage="encoding_request")
            secret = await backend._secret()
            trace("authorization_complete", elapsed_ms=round((time.monotonic() - started) * 1000))
            wire = await settle(asyncio.to_thread(build_generation_message, request, secret, base_version, spec))
            emit("preparing", stage="wire_validated")
            emit("preparing", stage="waiting_execution_lane")
            async with backend._lane:
                emit("preparing", stage="connecting_backend")
                await backend._connect()
                emit("preparing", stage="preparing_grpc_metadata")
                metadata = await backend._generation_metadata(wire.message)
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
                stage = SubmissionStage.POSSIBLY_SUBMITTED
                trace("submission_possible", elapsed_ms=round((time.monotonic() - started) * 1000))
                emit("submission_possible", stage="grpc_request")
                channel = backend._require_channel()
                async with channel.request(
                    GENERATE_METHOD,
                    Cardinality.UNARY_STREAM,
                    image_service.ImageGenerationRequest,
                    image_service.ImageGenerationResponse,
                    timeout=max(1.0, deadline - (time.monotonic() - started)),
                    metadata=metadata,
                ) as stream:
                    await stream.send_message(wire.message, end=True)
                    emit("preparing", stage="waiting_backend_response")
                    pending = bytearray()
                    async for response in stream:
                        messages += 1
                        observed = time.monotonic()
                        if first_response_ms is None:
                            first_response_ms = round((observed - started) * 1000)
                            trace("first_response", elapsed_ms=first_response_ms)
                        if first_content_ms is None and (
                            response.generated_images or response.generated_audio
                        ):
                            first_content_ms = round((observed - started) * 1000)
                            trace("first_content", elapsed_ms=first_content_ms)
                        if response.download_size:
                            declared_download_size = int(response.download_size)
                        if response.current_signpost:
                            name, value = betterproto.which_one_of(response.current_signpost, "signpost")
                            if name == "sampling":
                                last_stage = "sampling"
                                emit("progress", stage="sampling", completed=int(value.step), total=request.steps)
                            elif name:
                                last_stage = str(name)
                                emit("progress", stage=name)
                        for item in response.generated_audio or ():
                            audio.extend(item)
                        for data in response.generated_images:
                            pending.extend(data)
                            if response.chunk_state == image_service.ChunkState.MORE_CHUNKS:
                                continue
                            tensor = bytes(pending)
                            pending.clear()
                            width, height, rgb = await settle(asyncio.to_thread(_frame_rgb, tensor))
                            if (width, height) != (request.width_px, request.height_px):
                                raise _reject(request, "tensor_dimensions_mismatch", stage)
                            if poster is None:
                                poster = await settle(asyncio.to_thread(encode_png, rgb, width, height, 3))
                            await settle(asyncio.to_thread(sink.put, width, height, rgb))
                            frames += 1
                            received += len(tensor)
                            last_stage = "receiving_frames"
                            emit(
                                "progress",
                                stage="receiving_frames",
                                completed=frames,
                                total=request.num_frames,
                                received_bytes=received,
                            )
                        if observed - last_receive_log >= 10:
                            trace(
                                "receive_progress", elapsed_ms=round((observed - started) * 1000),
                                stage=last_stage, messages=messages, received_frames=frames,
                                received_tensor_bytes=received, audio_bytes=len(audio),
                                declared_download_bytes=declared_download_size, pending_bytes=len(pending),
                            )
                            last_receive_log = observed
                    stage = SubmissionStage.TERMINAL_OBSERVED
                    trace(
                        "receive_complete", elapsed_ms=round((time.monotonic() - started) * 1000),
                        messages=messages, requested_frames=request.num_frames,
                        received_frames=frames, tensor_bytes=received, audio_bytes=len(audio),
                        declared_download_bytes=declared_download_size, unfinished_bytes=len(pending),
                    )
                    if pending:
                        trace("unfinished_tensor", level=logging.WARNING, pending_bytes=len(pending))
                        raise _reject(request, "video_unfinished_tensor", stage)
            if frames == 0 or poster is None:
                raise _reject(request, "no_final_output", stage)
            if frames != request.num_frames:
                trace(
                    "integrity_failed", level=logging.WARNING, requested_frames=request.num_frames,
                    received_frames=frames, reason="incomplete_frame_count",
                )
                raise _reject(request, "video_frame_count_mismatch", stage)
            emit("progress", stage="encoding_video")
            encode_started = time.monotonic()
            trace("encode_started", received_frames=frames, playback_fps=playback_fps(request.model_id))
            await settle(asyncio.to_thread(sink.finish))
            trace(
                "encode_finished", duration_ms=round((time.monotonic() - encode_started) * 1000),
                exit_code=0,
            )
            video = sink.partial
            rate = audio_rate(request.model_id)
            if audio and rate:
                raw = work / "audio.f32"
                mux_started = time.monotonic()
                try:
                    audio_samples = await settle(asyncio.to_thread(_audio_raw, bytes(audio), raw))
                    seconds = frames / playback_fps(request.model_id)
                    inferred_audio_rate = round(audio_samples / seconds)
                    selected_audio_rate = (
                        rate if abs(inferred_audio_rate - rate) / rate <= 0.03 else inferred_audio_rate
                    )
                    trace(
                        "audio_mux_started", audio_samples=audio_samples, nominal_rate_hz=rate,
                        inferred_rate_hz=inferred_audio_rate, selected_rate_hz=selected_audio_rate,
                    )
                    muxed = work / "muxed.mp4.partial"
                    await settle(
                        asyncio.to_thread(video_encode.mux_audio, ffmpeg, video, raw, selected_audio_rate, muxed)
                    )
                    video = muxed
                    trace(
                        "audio_mux_finished", duration_ms=round((time.monotonic() - mux_started) * 1000),
                        exit_code=0,
                    )
                except Exception as exc:  # Retain a usable clip with an explicit silent-audio warning.
                    warnings.append("The sound could not be added; the clip has no audio.")
                    trace(
                        "audio_mux_failed", level=logging.WARNING,
                        reason=type(exc).__name__,
                        duration_ms=round((time.monotonic() - mux_started) * 1000),
                    )
            final = work / "video.mp4"
            video.replace(final)
            probe_started = time.monotonic()
            trace("probe_started")
            facts = await settle(asyncio.to_thread(video_encode.probe, ffprobe, final))
            trace(
                "probe_finished", duration_ms=round((time.monotonic() - probe_started) * 1000),
                probed_frames=facts.frames, width=facts.width, height=facts.height,
                fps=round(facts.fps, 3), duration_seconds=round(facts.duration, 3),
                has_audio=facts.has_audio, audio_rate_hz=facts.audio_rate, bytes=facts.size_bytes,
            )
            expected_duration = request.num_frames / playback_fps(request.model_id)
            mismatch = (
                facts.frames != frames
                or facts.frames != request.num_frames
                or (facts.width, facts.height) != (request.width_px, request.height_px)
                or abs(facts.fps - playback_fps(request.model_id)) > 0.1
                or abs(facts.duration - expected_duration) > max(0.15, expected_duration * 0.05)
            )
            trace(
                "integrity", level=logging.WARNING if mismatch else logging.DEBUG,
                requested_frames=request.num_frames, received_frames=frames, probed_frames=facts.frames,
                requested_width=request.width_px, requested_height=request.height_px,
                probed_width=facts.width, probed_height=facts.height,
                playback_fps=playback_fps(request.model_id), probed_fps=round(facts.fps, 3),
                expected_duration_seconds=round(expected_duration, 3),
                actual_duration_seconds=round(facts.duration, 3),
                has_audio=facts.has_audio, audio_samples=audio_samples,
                nominal_audio_rate_hz=rate, inferred_audio_rate_hz=inferred_audio_rate,
                selected_audio_rate_hz=selected_audio_rate,
                probed_audio_rate_hz=facts.audio_rate, mismatch=mismatch,
            )
            if mismatch:
                raise _reject(request, "video_integrity_mismatch", stage)
            if rate and not facts.has_audio and not warnings:
                warnings.append("DT+ did not provide usable audio; the clip has no audio.")
            emit("terminal", stage="OK")
        keep = True
        artifact = ImageArtifact(
            "final-1",
            Designation.FINAL,
            "image/png",
            poster,
            request.width_px,
            request.height_px,
            (
                ("video_temp_path", str(final)),
                ("video_frames", str(facts.frames)),
                ("video_width", str(facts.width)),
                ("video_height", str(facts.height)),
                ("video_fps", f"{facts.fps:.3f}"),
                ("video_duration_seconds", f"{facts.duration:.3f}"),
                ("video_has_audio", "yes" if facts.has_audio else "no"),
                ("video_audio_rate", str(facts.audio_rate or 0)),
                ("video_bytes", str(facts.size_bytes)),
            ),
        )
        server = backend._inventory.server_identity if backend._inventory is not None else "unknown"
        return GenerationResult(
            request.request_id,
            backend.backend_id,
            server,
            (artifact,),
            wire.effective_configuration,
            CompletionEvidence("OK", True),
            tuple(warnings),
        )
    except asyncio.CancelledError:
        trace(
            "local_stop", level=logging.WARNING, remote_cancellation_confirmed=False,
            submission_stage=stage.value, elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        raise
    except BackendError as exc:
        trace(
            "failed", level=logging.WARNING, code=exc.failure.code,
            submission_stage=stage.value, elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        raise
    except Exception as exc:
        trace(
            "failed", level=logging.WARNING, reason=type(exc).__name__,
            submission_stage=stage.value, elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        problem = from_exception(
            exc,
            phase="generation",
            operation="GenerateImage",
            request_id=request.request_id,
            stage=stage,
            trailers=stream is not None and getattr(stream, "trailing_metadata", None) is not None,
            private=(request.prompt, request.negative_prompt, *(metadata or {}).values()),
            response_headers=(
                *(getattr(stream, "initial_metadata", None) or {}).items(),
                *(getattr(stream, "trailing_metadata", None) or {}).items(),
            ),
            elapsed_ms=(time.monotonic() - started) * 1000,
        )
        raise BackendError(replace(problem, partial_artifacts=())) from None
    finally:
        backend._active -= 1
        try:
            await asyncio.shield(asyncio.to_thread(sink.finish, abort=True))
        except Exception:
            pass
        if not keep:
            import shutil

            shutil.rmtree(work, ignore_errors=True)


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _reject(request: ResolvedGenerationRequest, code: str, stage) -> BackendError:
    from dtflow.backends.drawthings.errors import api_failure, diagnostics
    from dtflow.core.models import RemoteOutcome

    return BackendError(
        api_failure(
            ErrorCategory.PROTOCOL,
            code,
            "Received video output was rejected.",
            request.request_id,
            stage,
            RemoteOutcome.UNKNOWN,
            diagnostics=diagnostics(phase="generation", reason=code),
            operation="GenerateImage",
            transport="grpc",
        )
    )
