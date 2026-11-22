"""One video generation on Draw Things: send, receive N one-frame tensors plus an optional audio tensor, encode an MP4.

The image path (``DrawThingsBackend.generate``) keeps its own limits; a clip is far bigger (86 MiB at 384x640) and
takes minutes (up to 243 s seen), so this path has its own receive deadline and no transport cap. Received frame
tensors are written to a disk spool in arrival order. Once the stream ends, the clip waits (cancellably) for a local
encoder slot, then ffmpeg encodes from the spool. The result carries the poster (first frame, PNG) as the only final
artifact, plus the temporary MP4 path and the probed facts in the artifact metadata; the artifact saver
(``services.video_artifacts``) moves the MP4 beside the poster.
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
import tempfile
import threading
import time
from contextlib import AsyncExitStack, closing
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import betterproto
from drawthings_py.generated.dt_grpc import image_service
from grpclib.const import Cardinality

from dtflow.backends.drawthings.errors import error, from_exception
from dtflow.backends.drawthings.versions import bundled_base_version
from dtflow.backends.drawthings.wire import build_generation_message
from dtflow.core import provider_phase
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
from dtflow.diagnostics import api_error_data, event as diagnostic_event
from dtflow.services import video_encode
from dtflow.services.video_resources import (
    AUDIO_LIMIT,
    MP4_LIMIT,
    SPOOL_RESERVE,
    TENSOR_LIMIT,
    grow_video_spool,
    recheck_video_storage,
)

log = logging.getLogger(__name__)
GENERATE_METHOD = "/ImageGenerationService/GenerateImage"
# Temporary Windows A/B experiment; keep the established decoder as the default.
_USE_LUT_DECODER = os.environ.get("DTFLOW_VIDEO_DECODER") == "lut"


@lru_cache(maxsize=1)
def _half_to_byte_lut():
    """Map every float16 bit pattern using the exact drawthings-py 0.4.0 pixel formula."""
    import numpy as np

    values = np.arange(1 << 16, dtype=np.uint16).view(np.float16)
    # NaN tensors are rejected before lookup. Ignore warnings for unused NaN entries.
    with np.errstate(invalid="ignore", over="ignore"):
        return np.clip(np.round((values + 1) * 127.5), 0, 255).astype(np.uint8)


def _frame_rgb_lut(tensor: bytes, width: int, height: int, channels: int) -> bytes:
    """Experimental video-only decoder: preserve FPZIP handling, replace per-pixel float arithmetic with lookup."""
    import fpzip
    import numpy as np

    count = width * height * channels
    if struct.unpack_from("<I", tensor)[0] == 1012247:  # drawthings-py 0.4.0 FPZIP magic
        values = np.asarray(fpzip.decompress(tensor[68:], order="C"), dtype=np.float16).reshape(-1)[:count]
    else:
        expected = 68 + count * 2
        if len(tensor) < expected:
            raise ValueError(f"Tensor payload is too short: expected {expected} bytes, got {len(tensor)} bytes")
        values = np.frombuffer(tensor, dtype="<f2", count=count, offset=68)
    if values.size != count:
        raise ValueError(f"Tensor payload size mismatch: expected {count} values, got {values.size}")
    if np.isnan(values).any():
        raise ValueError("Tensor payload contains NaN values")

    pixels = _half_to_byte_lut()[values.view(np.uint16)].reshape(height, width, channels)
    return np.ascontiguousarray(pixels[:, :, :3]).tobytes()


def _frame_rgb(tensor: bytes) -> tuple[int, int, bytes]:
    """One received frame tensor to packed RGB bytes (worker thread)."""
    if len(tensor) < 68:
        raise ValueError("Video tensor header is truncated.")
    height, width, channels = struct.unpack_from("<3I", tensor, 24)
    if not width or not height or width * height > 2048 * 2048 or channels not in (3, 4):
        raise ValueError("Video frame exceeds the bounded pixel/channel policy.")
    if _USE_LUT_DECODER:
        return width, height, _frame_rgb_lut(tensor, width, height, channels)

    import numpy as np
    from drawthings_py.image.image_buffer import ImageBuffer

    buffer = ImageBuffer.from_tensor(tensor)
    if buffer.channels == 3:
        return buffer.width, buffer.height, buffer.data  # already packed RGB: avoid a per-frame copy
    pixels = np.frombuffer(buffer.data, dtype=np.uint8).reshape(buffer.height, buffer.width, buffer.channels)
    return buffer.width, buffer.height, np.ascontiguousarray(pixels[:, :, :3]).tobytes()


def _tensor_size(tensor: bytes) -> tuple[int, int] | None:
    """Width and height from a received tensor's header, or None when it is truncated or outside the policy."""
    if len(tensor) < 68:
        return None
    height, width, channels = struct.unpack_from("<3I", tensor, 24)
    if not width or not height or width * height > 2048 * 2048 or channels not in (3, 4):
        return None
    return width, height


def _encode_spool(spool: video_encode.TensorSpool, sink: video_encode.FrameSink, stop: threading.Event) -> bytes:
    """Feed the spooled tensors, in order, through the encoder and return the poster PNG (worker thread).

    The first frame's decode also makes the poster; ffmpeg starts here, not while receiving. ``stop`` is set when the
    item is cancelled: the loop then ends at the next frame.
    """
    poster = None
    log.info("Video decoder: %s", "lut (experimental)" if _USE_LUT_DECODER else "drawthings-py")
    try:
        with closing(spool.read()) as tensors:
            for tensor in tensors:
                if stop.is_set():
                    raise video_encode.EncodeError("Local encoding was cancelled.")
                width, height, rgb = _frame_rgb(tensor)
                if poster is None:
                    poster = encode_png(rgb, width, height, 3)
                sink.put(width, height, rgb)
    except OSError as exc:
        raise video_encode.EncodeError(
            f"The received frame data could not be read back ({type(exc).__name__})."
        ) from exc
    sink.finish()
    if poster is None:
        raise video_encode.EncodeError("No frames were available to encode.")
    return poster


async def _encode_cancellably(spool: video_encode.TensorSpool, sink: video_encode.FrameSink) -> bytes:
    """Run the encode worker; on cancellation stop it and kill ffmpeg so it ends promptly, then re-raise."""
    stop = threading.Event()
    task = asyncio.ensure_future(video_encode.run_worker(_encode_spool, spool, sink, stop))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        sink.interrupt()
        while not task.done():  # the worker returns within a frame once ffmpeg is gone
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError:
                pass
        if not task.cancelled():
            task.exception()  # retrieved: its error is the cancellation itself
        raise


def _audio_raw(data: bytes, target: Path) -> int:
    """Write the audio tensor as raw float32 little-endian stereo and return its sample count (worker thread)."""
    import numpy as np
    from drawthings_py.grpc.audio import AudioBuffer

    if len(data) < 68 or not 0 < struct.unpack_from("<I", data, 24)[0] <= AUDIO_LIMIT // 8:
        raise ValueError("Video audio exceeds the bounded stereo sample policy.")
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
                ErrorCategory.VALIDATION,
                "video_start_permit_missing",
                "The selected checkpoint has no exact owner-verified start-image permit.",
                request.request_id,
            )
        try:
            permit.validate_scope(
                backend.backend_id,
                backend.endpoint.label,
                request.model_id,
                "img2img",
            )
        except ValueError:
            raise error(
                ErrorCategory.VALIDATION,
                "video_start_permit_mismatch",
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
    spool = video_encode.TensorSpool(
        work / "frames.spool", SPOOL_RESERVE, lambda chunk: grow_video_spool(request.request_id, chunk)
    )
    slot = AsyncExitStack()  # holds the local encoder slot from the wait until the encode and audio mux end
    frames = 0
    received = 0
    audio = bytearray()
    poster: bytes | None = None
    keep = False
    messages = 0
    declared_download_size: int | None = None
    last_receive_log = started
    last_beat = started
    first_response_ms: float | None = None
    first_content_ms: float | None = None
    last_stage = "preparing"
    audio_samples = 0
    inferred_audio_rate: int | None = None
    selected_audio_rate: int | None = None
    warnings: list[str] = []

    def trace(name: str, *, level: int = logging.DEBUG, **data) -> None:
        diagnostic_event(
            log,
            f"video.{name}",
            level=level,
            data={"request_id": request.request_id, **data},
        )

    trace(
        "pre_submit",
        model_id=request.model_id,
        requested_frames=request.num_frames,
        requested_width=request.width_px,
        requested_height=request.height_px,
        playback_fps=playback_fps(request.model_id),
        audio_rate_hz=audio_rate(request.model_id),
        profile_available=video_profile(request.model_id) is not None,
        start_image_present=request.start_image is not None,
        start_image_permit="owner_verified" if request.start_image else "not_required",
        ffmpeg_available=True,
        ffprobe_available=True,
    )

    def emit(kind: str, **payload) -> None:
        nonlocal sequence
        sequence += 1
        on_event(BackendEvent(request.request_id, sequence, kind, _now(), **payload))

    backend._active += 1
    try:
        async with asyncio.timeout(deadline) as receive_deadline:
            emit("preparing", stage="video_preparing")
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
            trace("credentials_resolved", elapsed_ms=round((time.monotonic() - started) * 1000))
            wire = await settle(video_encode.run_worker(build_generation_message, request, secret, base_version, spec))
            emit("preparing", stage="wire_validated")
            emit("preparing", stage="waiting_execution_lane")
            async with backend._lane:
                emit("preparing", stage="connecting_backend")
                await backend._connect()
                emit("preparing", stage="preparing_grpc_metadata")
                metadata = await backend._generation_metadata(wire.message)
                trace("authorization_complete", elapsed_ms=round((time.monotonic() - started) * 1000))
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
                try:
                    await settle(asyncio.to_thread(recheck_video_storage, request.request_id))
                except (OSError, ValueError):
                    raise error(
                        ErrorCategory.RESOURCE_LIMIT,
                        "video_storage_limit",
                        "Video storage became unavailable before submission. Nothing was sent.",
                        request.request_id,
                    ) from None
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
                        if (
                            frames == 0
                            and not response.generated_images
                            and not response.generated_audio
                            and observed - last_beat >= 5
                        ):
                            # DT+ keeps sending messages while it works; each one lets the waiting estimate advance.
                            last_beat = observed
                            emit("progress", stage="waiting_heartbeat")
                        if first_response_ms is None:
                            first_response_ms = round((observed - started) * 1000)
                            trace("first_response", elapsed_ms=first_response_ms)
                        if first_content_ms is None and (response.generated_images or response.generated_audio):
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
                            if len(audio) + len(item) > AUDIO_LIMIT:
                                raise _reject(request, "video_audio_limit", stage)
                            audio.extend(item)
                        for data in response.generated_images:
                            if len(pending) + len(data) > TENSOR_LIMIT or frames >= request.num_frames:
                                raise _reject(request, "video_tensor_limit", stage)
                            pending.extend(data)
                            if response.chunk_state == image_service.ChunkState.MORE_CHUNKS:
                                continue
                            tensor = bytes(pending)
                            pending.clear()
                            size = _tensor_size(tensor)
                            if size is None:
                                raise _reject(request, "video_tensor_invalid", stage)
                            if size != (request.width_px, request.height_px):
                                raise _reject(request, "tensor_dimensions_mismatch", stage)
                            try:
                                await settle(video_encode.run_worker(spool.append, tensor))
                            except OSError as exc:
                                raise video_encode.EncodeError(
                                    f"The received frames could not be written to disk ({type(exc).__name__})."
                                ) from exc
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
                                "receive_progress",
                                elapsed_ms=round((observed - started) * 1000),
                                stage=last_stage,
                                messages=messages,
                                received_frames=frames,
                                received_tensor_bytes=received,
                                audio_bytes=len(audio),
                                declared_download_bytes=declared_download_size,
                                pending_bytes=len(pending),
                            )
                            last_receive_log = observed
                    stage = SubmissionStage.TERMINAL_OBSERVED
                    trace(
                        "receive_complete",
                        elapsed_ms=round((time.monotonic() - started) * 1000),
                        messages=messages,
                        requested_frames=request.num_frames,
                        received_frames=frames,
                        tensor_bytes=received,
                        audio_bytes=len(audio),
                        declared_download_bytes=declared_download_size,
                        unfinished_bytes=len(pending),
                    )
                    provider_phase.provider_done()  # received everything: decoding, encoding and saving are local
                    if pending:
                        trace("unfinished_tensor", level=logging.WARNING, pending_bytes=len(pending))
                        raise _reject(request, "video_unfinished_tensor", stage)
            if frames == 0:
                raise _reject(request, "no_final_output", stage)
            if frames != request.num_frames:
                trace(
                    "integrity_failed",
                    level=logging.WARNING,
                    requested_frames=request.num_frames,
                    received_frames=frames,
                    reason="incomplete_frame_count",
                )
                raise _reject(request, "video_frame_count_mismatch", stage)
            # The stream is closed and the receive deadline has done its job: waiting for a local encoder
            # (cancellable) never expires an already downloaded clip. ffmpeg keeps its own bounded timeouts.
            receive_deadline.reschedule(None)
            await settle(video_encode.run_worker(spool.finish_writing))
            trace("spool_ready", received_frames=frames, spool_bytes=spool.size)
            wait_started = time.monotonic()
            await slot.enter_async_context(
                video_encode.encoder_slot(on_wait=lambda: emit("progress", stage="waiting_local_encoder"))
            )
            emit("progress", stage="encoding_video")
            encode_started = time.monotonic()
            trace(
                "encode_started",
                received_frames=frames,
                playback_fps=playback_fps(request.model_id),
                waited_ms=round((encode_started - wait_started) * 1000),
                codec_threads=video_encode.effective_threads(),
                max_encoders=video_encode.policy_encoders() or "unlimited",
            )
            poster = await _encode_cancellably(spool, sink)
            await settle(video_encode.run_worker(spool.delete))
            trace(
                "encode_finished",
                duration_ms=round((time.monotonic() - encode_started) * 1000),
                exit_code=0,
            )
            video = sink.partial
            rate = audio_rate(request.model_id)
            if audio and rate:
                raw = work / "audio.f32"
                mux_started = time.monotonic()
                try:
                    audio_samples = await settle(video_encode.run_worker(_audio_raw, bytes(audio), raw))
                    seconds = frames / playback_fps(request.model_id)
                    inferred_audio_rate = round(audio_samples / seconds)
                    selected_audio_rate = (
                        rate if abs(inferred_audio_rate - rate) / rate <= 0.03 else inferred_audio_rate
                    )
                    trace(
                        "audio_mux_started",
                        audio_samples=audio_samples,
                        nominal_rate_hz=rate,
                        inferred_rate_hz=inferred_audio_rate,
                        selected_rate_hz=selected_audio_rate,
                    )
                    muxed = work / "muxed.mp4.partial"
                    await settle(
                        video_encode.run_worker(video_encode.mux_audio, ffmpeg, video, raw, selected_audio_rate, muxed)
                    )
                    video = muxed
                    trace(
                        "audio_mux_finished",
                        duration_ms=round((time.monotonic() - mux_started) * 1000),
                        exit_code=0,
                    )
                except video_encode.VideoSizeLimitError:
                    raise
                except Exception as exc:  # Retain a usable clip with an explicit silent-audio warning.
                    warnings.append("The sound could not be added; the clip has no audio.")
                    evidence = (
                        api_error_data(
                            message=str(exc),
                            private=(str(work), str(raw), request.prompt, request.negative_prompt),
                        )
                        if isinstance(exc, video_encode.EncodeError) and log.isEnabledFor(logging.DEBUG)
                        else {}
                    )
                    trace(
                        "audio_mux_failed",
                        level=logging.WARNING,
                        reason=type(exc).__name__,
                        duration_ms=round((time.monotonic() - mux_started) * 1000),
                        **evidence,
                    )
            await slot.aclose()  # encoding and muxing are done; verification below needs no encoder slot
            emit("progress", stage="verifying_video")
            final = work / "video.mp4"
            video.replace(final)
            probe_started = time.monotonic()
            trace("probe_started")
            facts = await settle(video_encode.run_worker(video_encode.probe, ffprobe, final))
            if facts.size_bytes >= MP4_LIMIT:
                raise video_encode.VideoSizeLimitError("Video reached its local MP4 size limit.")
            trace(
                "probe_finished",
                duration_ms=round((time.monotonic() - probe_started) * 1000),
                probed_frames=facts.frames,
                width=facts.width,
                height=facts.height,
                fps=round(facts.fps, 3),
                duration_seconds=round(facts.duration, 3),
                has_audio=facts.has_audio,
                audio_rate_hz=facts.audio_rate,
                bytes=facts.size_bytes,
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
                "integrity",
                level=logging.WARNING if mismatch else logging.DEBUG,
                requested_frames=request.num_frames,
                received_frames=frames,
                probed_frames=facts.frames,
                requested_width=request.width_px,
                requested_height=request.height_px,
                probed_width=facts.width,
                probed_height=facts.height,
                playback_fps=playback_fps(request.model_id),
                probed_fps=round(facts.fps, 3),
                expected_duration_seconds=round(expected_duration, 3),
                actual_duration_seconds=round(facts.duration, 3),
                has_audio=facts.has_audio,
                audio_samples=audio_samples,
                nominal_audio_rate_hz=rate,
                inferred_audio_rate_hz=inferred_audio_rate,
                selected_audio_rate_hz=selected_audio_rate,
                probed_audio_rate_hz=facts.audio_rate,
                mismatch=mismatch,
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
            "local_stop",
            level=logging.WARNING,
            remote_cancellation_confirmed=False,
            submission_stage=stage.value,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        raise
    except BackendError as exc:
        trace(
            "failed",
            level=logging.WARNING,
            code=exc.failure.code,
            submission_stage=stage.value,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        raise
    except video_encode.VideoSizeLimitError:
        trace("failed", level=logging.WARNING, code="video_size_limit", submission_stage=stage.value)
        raise _reject(
            request,
            "video_size_limit",
            stage,
            category=ErrorCategory.RESOURCE_LIMIT,
            message="Video reached the local MP4 size limit and was not saved. Provider work may already have been charged.",
        ) from None
    except video_encode.EncodeError as exc:
        # Local failure (disk or ffmpeg) after or while receiving: say so, never pass it off as a provider rejection.
        evidence = (
            api_error_data(
                message=str(exc),
                private=(str(work), str(sink.partial), request.prompt, request.negative_prompt),
            )
            if log.isEnabledFor(logging.DEBUG)
            else {}
        )
        local_stage = "encoding" if stage is SubmissionStage.TERMINAL_OBSERVED else "receiving"
        trace(
            "failed",
            level=logging.WARNING,
            reason=type(exc).__name__,
            local_stage=local_stage,
            submission_stage=stage.value,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            **evidence,
        )
        reason = (str(exc).splitlines() or [""])[0].replace(str(work), "<temp>")[:160]
        raise _reject(
            request,
            "video_local_failure",
            stage,
            category=ErrorCategory.RESOURCE_LIMIT,
            message=f"Local video processing failed while {local_stage} ({reason}). Provider work may already have been charged.",
        ) from None
    except Exception as exc:
        evidence = (
            api_error_data(
                message=str(exc),
                private=(str(work), str(sink.partial), request.prompt, request.negative_prompt),
            )
            if isinstance(exc, video_encode.EncodeError) and log.isEnabledFor(logging.DEBUG)
            else {}
        )
        trace(
            "failed",
            level=logging.WARNING,
            reason=type(exc).__name__,
            submission_stage=stage.value,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            **evidence,
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
            # Each step is best effort and none may skip the next: the spool handle must be closed before rmtree.
            for step in (
                lambda: asyncio.to_thread(sink.finish, abort=True),  # FFmpeg is gone before its slot is released
                slot.aclose,
                lambda: video_encode.run_worker(spool.delete),
            ):
                try:
                    await settle(step())
                except Exception:
                    pass
        finally:
            if not keep:
                import shutil

                shutil.rmtree(work, ignore_errors=True)


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _reject(
    request: ResolvedGenerationRequest,
    code: str,
    stage,
    *,
    category=ErrorCategory.PROTOCOL,
    message="Received video output was rejected.",
) -> BackendError:
    from dtflow.backends.drawthings.errors import api_failure, diagnostics
    from dtflow.core.models import RemoteOutcome

    return BackendError(
        api_failure(
            category,
            code,
            message,
            request.request_id,
            stage,
            RemoteOutcome.UNKNOWN,
            diagnostics=diagnostics(phase="generation", reason=code),
            operation="GenerateImage",
            transport="grpc",
        )
    )
