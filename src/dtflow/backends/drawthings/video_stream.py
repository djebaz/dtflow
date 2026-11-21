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
from dtflow.core.video import VIDEO_TOTAL_TIMEOUT, audio_rate, playback_fps
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
                        if response.current_signpost:
                            name, value = betterproto.which_one_of(response.current_signpost, "signpost")
                            if name == "sampling":
                                emit("progress", stage="sampling", completed=int(value.step), total=request.steps)
                            elif name:
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
                            emit(
                                "progress",
                                stage="receiving_frames",
                                completed=frames,
                                total=request.num_frames,
                                received_bytes=received,
                            )
                    stage = SubmissionStage.TERMINAL_OBSERVED
            if frames == 0 or poster is None:
                raise _reject(request, "no_final_output", stage)
            emit("progress", stage="encoding_video")
            await settle(asyncio.to_thread(sink.finish))
            video = sink.partial
            rate = audio_rate(request.model_id)
            if audio and rate:
                raw = work / "audio.f32"
                try:
                    samples = await settle(asyncio.to_thread(_audio_raw, bytes(audio), raw))
                    seconds = frames / playback_fps(request.model_id)
                    used = rate if abs(samples / seconds - rate) / rate <= 0.03 else round(samples / seconds)
                    muxed = work / "muxed.mp4.partial"
                    await settle(asyncio.to_thread(video_encode.mux_audio, ffmpeg, video, raw, used, muxed))
                    video = muxed
                except Exception as exc:  # the clip is still good without sound; say so in the result
                    diagnostic_event(
                        log, "video.audio_mux_failed", level=logging.WARNING, data={"error": type(exc).__name__}
                    )
            final = work / "video.mp4"
            video.replace(final)
            facts = await settle(asyncio.to_thread(video_encode.probe, ffprobe, final))
            if facts.frames != frames:
                raise _reject(request, "video_frame_count_mismatch", stage)
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
            () if facts.has_audio or not audio else ("The sound could not be added; the clip has no audio.",),
        )
    except BackendError:
        raise
    except Exception as exc:
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
