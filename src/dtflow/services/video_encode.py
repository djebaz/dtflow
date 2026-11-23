"""ffmpeg and ffprobe work for video generation: frame sink, audio mux, probe, tool discovery.

Proven by the video POC on DT+ (libx264 high 4.1, yuv420p, faststart, AAC mux). Everything here blocks, so callers run
it in worker threads. Frozen Windows builds use their packaged tools exclusively; Windows source development uses
the same pinned provider staged by ``tools/packaging/ffmpeg_bundle.py`` (never ``PATH``); other platforms use ``PATH``.
No console window opens on Windows.
"""

from __future__ import annotations

import asyncio
import contextvars
import importlib.util
import json
import logging
import os
import queue
import shutil
import struct
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from dtflow.core.models import MAX_QUEUE_CONCURRENCY
from dtflow.services.video_resources import MP4_LIMIT

log = logging.getLogger(__name__)

_VIDEO_WORKERS = ThreadPoolExecutor(max_workers=MAX_QUEUE_CONCURRENCY, thread_name_prefix="dtflow-video")

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
STALL_SECONDS = 90  # a frame writer that makes no progress for this long fails the run

# Temporary A/B decoder selection for issue #562. No persisted setting or UI control.
video_decoder = "lut"


def set_video_decoder(mode: str) -> None:
    """Select the video tensor decoder for this process before GUI startup."""
    global video_decoder
    video_decoder = mode


async def run_worker(function, *args, **kwargs):
    """Bound video conversion/encoding work separately from the runner's DB/control executor."""
    context = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        _VIDEO_WORKERS, context.run, partial(function, *args, **kwargs)
    )


# Local encoding policy (#550): ``(max encoders, codec threads)``, 0 meaning Unlimited / Auto. Read when an encoder
# starts, so a change never retunes a running one.
_policy: tuple[int, int] = (0, 0)
_gate = threading.Lock()
_running = 0
_waiters: list[asyncio.Future] = []


def set_policy(encoders: int, threads: int) -> None:
    """Apply the saved or just-changed Connection preferences to encoders that have not started yet."""
    global _policy
    _policy = (encoders, threads)
    _wake()  # a raised cap must release waiting items without needing another encoder to finish


def apply_saved_policy(state: Path) -> None:
    """Headless runs: load the Connection preferences from the state directory (defaults when unreadable)."""
    from dtflow.persistence.settings import SettingsError, load_video_encoding

    try:
        set_policy(*load_video_encoding(state))
    except SettingsError:
        set_policy(0, 0)


def auto_threads(cores: int | None = None) -> int:
    """Codec threads chosen from the CPU count: about half the cores, at least two where there are two, at most 8."""
    if cores is None:
        cores = (os.process_cpu_count() if hasattr(os, "process_cpu_count") else os.cpu_count()) or 1
    return max(1, min(cores, 2), min(8, cores // 2))


def policy_encoders() -> int:
    return _policy[0]


def effective_threads() -> int:
    return _policy[1] or auto_threads()


def _wake() -> None:
    with _gate:
        waiting = list(_waiters)
    for future in waiting:
        future.get_loop().call_soon_threadsafe(lambda f=future: f.done() or f.set_result(None))


@asynccontextmanager
async def encoder_slot(on_wait=None):
    """One slot of the shared local-encoder limit, held across a clip's FFmpeg encode and audio mux.

    Waiting is cancellable. ``on_wait`` is called once, only when the item has to wait. With no cap (0) the slot is
    only counted, never refused. The cap is read at each attempt, so a changed setting applies to waiting items.
    """
    global _running
    waited = False
    while True:
        with _gate:
            cap = _policy[0]
            if cap == 0 or _running < cap:
                _running += 1
                break
            future = asyncio.get_running_loop().create_future()
            _waiters.append(future)
        if not waited:
            waited = True
            if on_wait is not None:
                on_wait()
        try:
            await future
        finally:
            with _gate:
                if future in _waiters:
                    _waiters.remove(future)
    try:
        yield waited
    finally:
        with _gate:
            _running -= 1
        _wake()


PROVIDER_ENV = "DTFLOW_FFMPEG_PROVIDER"
_SETUP_COMMAND = "uv run python tools/packaging/ffmpeg_bundle.py"
_verified: set[str] = set()


def _source_windows() -> bool:
    return os.name == "nt" and not getattr(sys, "frozen", False)


def _bundle_module():
    """Load the packaging module that pins and verifies the providers (source checkouts only)."""
    path = Path(__file__).resolve().parents[3] / "tools" / "packaging" / "ffmpeg_bundle.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("dtflow_ffmpeg_bundle", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("dtflow_ffmpeg_bundle", module)
    spec.loader.exec_module(module)
    return module


def selected_provider() -> str:
    """The pinned provider for Windows source runs: ``imageio-mini`` unless explicitly overridden."""
    value = os.environ.get(PROVIDER_ENV, "").strip()
    return value or "imageio-mini"


def _staged_folder() -> Path | str:
    """The selected provider's staged ``bin`` folder, or a problem message."""
    provider = selected_provider()
    module = _bundle_module()
    if module is None:
        return "The pinned FFmpeg setup script is missing; run DTFlow from a complete source checkout."
    if provider not in module.PROVIDERS:
        return f"Unknown {PROVIDER_ENV} value {provider!r}; use one of: {', '.join(module.PROVIDERS)}."
    return module.stage_dir(provider) / "bin"


def _setup_hint(provider: str) -> str:
    suffix = "" if provider == "imageio-mini" else f" --provider {provider}"
    return f"Run `{_SETUP_COMMAND}{suffix}` once to stage the pinned FFmpeg and ffprobe."


def find_tools() -> tuple[str, str] | None:
    """Use bundled frozen executables, the staged pinned provider on Windows source runs, or PATH elsewhere."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base is None:
            return None
        folder = Path(base) / "ffmpeg"
    elif _source_windows():
        folder = _staged_folder()
        if isinstance(folder, str):
            return None
    else:
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        return (ffmpeg, ffprobe) if ffmpeg and ffprobe else None
    ffmpeg, ffprobe = folder / "ffmpeg.exe", folder / "ffprobe.exe"
    return (str(ffmpeg), str(ffprobe)) if ffmpeg.is_file() and ffprobe.is_file() else None


def tools_problem() -> str | None:
    """A cheap path check for UI presentation (never starts a subprocess or hashes files on Qt)."""
    if find_tools() is not None:
        return None
    if getattr(sys, "frozen", False):
        return "This DTFlow build is missing its bundled FFmpeg tools. Reinstall a complete Windows build."
    if _source_windows():
        folder = _staged_folder()
        if isinstance(folder, str):
            return folder
        return f"Pinned FFmpeg ({selected_provider()}) is not staged at {folder}. {_setup_hint(selected_provider())}"
    return "ffmpeg and ffprobe were not found on PATH; install them before generating video."


def _staging_problem() -> str | None:
    """Verify the staged provider's manifest and file hashes once per process (worker thread only)."""
    provider = selected_provider()
    if provider in _verified:
        return None
    try:
        _bundle_module().staged_record(provider)
    except (OSError, ValueError, KeyError) as exc:
        return f"Staged FFmpeg ({provider}) failed verification: {str(exc).rstrip('.')}. {_setup_hint(provider)}"
    _verified.add(provider)
    return None


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else "unknown"


_ready: set[tuple[str, str]] = set()
_ready_lock = threading.Lock()


def tools_readiness_problem() -> str | None:
    """Verify usable H.264/AAC encoding and probing on the generation worker before submission.

    A success is remembered for this process and the exact FFmpeg/FFprobe pair, so later videos (and concurrent
    attempts, which wait on the same lock) do not launch the tools again. Failures are never remembered.
    """
    if (problem := tools_problem()) is not None:
        return problem
    if _source_windows() and (problem := _staging_problem()) is not None:
        return problem
    pair = find_tools()
    with _ready_lock:
        if pair in _ready:
            return None
        ffmpeg, ffprobe = pair
        try:
            encoders = run_tool([ffmpeg, "-hide_banner", "-encoders"], timeout=15)
            probe_version = run_tool([ffprobe, "-version"], timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"Video tools cannot run ({type(exc).__name__}); generation was not submitted."
        if encoders.returncode != 0 or probe_version.returncode != 0:
            return "FFmpeg or ffprobe could not start; generation was not submitted."
        if " libx264 " not in encoders.stdout or " aac " not in encoders.stdout:
            return "FFmpeg lacks the H.264 (libx264) or AAC encoder required for video generation."
        _ready.add(pair)
    mode = (
        "frozen" if getattr(sys, "frozen", False) else f"source:{selected_provider()}" if _source_windows() else "path"
    )
    log.info("video tools (%s) ffmpeg=%s ffprobe=%s | %s", mode, ffmpeg, ffprobe, _first_line(probe_version.stdout))
    return None


def quiet_process_kwargs() -> dict:
    if os.name != "nt":
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0  # SW_HIDE
    return {"creationflags": NO_WINDOW, "startupinfo": info}


def run_tool(command: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        **quiet_process_kwargs(),
    )


def tail(text: str, count: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-count:])


class EncodeError(RuntimeError):
    """ffmpeg or ffprobe failed; the message carries the tail of its own output."""


class VideoSizeLimitError(EncodeError):
    """The encoder/mux safety stop was reached; never present its truncated output as a finished clip."""


def _size_limit_reached(path: Path) -> bool:
    try:
        return path.stat().st_size >= MP4_LIMIT
    except FileNotFoundError:
        return False


class TensorSpool:
    """Received frame tensors on disk, in order, so receiving never waits for (or starts) FFmpeg.

    Each record is a 4-byte little-endian length and the original tensor bytes. Appending runs in a worker thread in
    frame order; reading yields them back in the same order. Deleting it is part of the item's own cleanup.
    """

    CHECK_EVERY = 16 * 1024 * 1024  # re-check free space after this much growth beyond the admission estimate

    def __init__(self, path: Path, estimate: int, grow: Callable[[int], None]):
        """``grow(chunk)`` raises OSError unless the disk can take ``chunk`` more bytes beyond the estimate."""
        self.path, self.estimate, self.grow = Path(path), estimate, grow
        self.count = 0
        self.size = 0
        self._file = None
        self._checked = estimate

    def append(self, tensor: bytes) -> None:
        if self._file is None:
            self._file = self.path.open("wb")
        self._file.write(struct.pack("<I", len(tensor)))
        self._file.write(tensor)
        self.count += 1
        self.size += 4 + len(tensor)
        if self.size > self._checked:
            # The reservation is an estimate, not a cap: a larger legitimate clip continues while space remains.
            self._checked = self.size + self.CHECK_EVERY
            self.grow(self.CHECK_EVERY)

    def finish_writing(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def read(self) -> Iterator[bytes]:
        with self.path.open("rb") as stream:
            for _ in range(self.count):
                header = stream.read(4)
                if len(header) != 4:
                    raise EncodeError("The received frame data on disk is incomplete.")
                (length,) = struct.unpack("<I", header)
                tensor = stream.read(length)
                if len(tensor) != length:
                    raise EncodeError("The received frame data on disk is incomplete.")
                yield tensor

    def delete(self) -> None:
        """Close the handle, then remove the file (an open handle blocks deleting the directory on Windows)."""
        self.finish_writing()
        self.path.unlink(missing_ok=True)


class FrameSink:
    """Frames leave memory at once: a writer thread feeds ffmpeg's stdin through a small queue.

    ffmpeg's stderr is drained in its own thread (an unread pipe freezes it at about 64 KB), a stalled writer is
    detected by a timed queue put, and a writer that does not finish after the end is killed with the process.
    """

    def __init__(self, ffmpeg: str, fps: int, partial: Path):
        self.ffmpeg, self.fps, self.partial = ffmpeg, fps, partial
        self.queue: queue.Queue = queue.Queue(maxsize=2)
        self.frames = 0
        self.size: tuple[int, int] | None = None
        self.first_frame: bytes | None = None
        self._proc: subprocess.Popen | None = None
        self._writer: threading.Thread | None = None
        self._drain: threading.Thread | None = None
        self._stderr: deque[str] = deque(maxlen=60)
        self._failed: str | None = None
        self._done = False

    def _start(self, width: int, height: int) -> None:
        self.size = (width, height)
        bitrate = max(9_500_000, width * height * 5)
        command = [
            self.ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(self.fps), "-i", "pipe:0",
            "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.1", "-pix_fmt", "yuv420p",
            "-b:v", str(bitrate), "-g", "30", "-bf", "2", "-threads", str(effective_threads()), "-filter_threads", "1",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-movflags", "+faststart", "-fs", str(MP4_LIMIT), "-f", "mp4", str(self.partial),
        ]  # fmt: skip
        self._proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **quiet_process_kwargs()
        )
        self._drain = threading.Thread(target=self._drain_stderr, daemon=True)
        self._drain.start()
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._writer.start()

    def _drain_stderr(self) -> None:
        for raw in iter(self._proc.stderr.readline, b""):
            self._stderr.append(raw.decode("utf-8", "replace").rstrip())

    def _write_loop(self) -> None:
        stdin = self._proc.stdin
        try:
            while (item := self.queue.get()) is not None:
                if self._failed:
                    continue
                try:
                    stdin.write(item)
                    self.frames += 1
                except Exception as exc:
                    self._failed = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                stdin.close()
            except OSError:
                pass

    def put(self, width: int, height: int, rgb: bytes) -> None:
        """Called from a worker thread, in frame order."""
        if self._writer is None:
            self.first_frame = rgb
            self._start(width, height)
        elif (width, height) != self.size:
            raise EncodeError(f"A frame is {width}x{height}, the first was {self.size}.")
        if self._failed:
            if _size_limit_reached(self.partial):
                raise VideoSizeLimitError("Video reached its local MP4 size limit.")
            raise EncodeError("The frame writer failed: " + self._failed)
        try:
            self.queue.put(rgb, timeout=STALL_SECONDS)
        except queue.Full:
            if _size_limit_reached(self.partial):
                raise VideoSizeLimitError("Video reached its local MP4 size limit.") from None
            raise EncodeError(f"The frame writer made no progress for {STALL_SECONDS} s.") from None

    def interrupt(self) -> None:
        """Kill ffmpeg at once so a blocked writer or a long encode ends; finish(abort=True) still does the cleanup."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def finish(self, *, abort: bool = False) -> None:
        """Close the writer and ffmpeg; abort (or any failure) kills ffmpeg and removes the half-written file."""
        if self._done:
            return
        self._done = True
        requested_abort = abort
        error = None

        def stop_writer() -> None:
            # Wake a writer blocked on the pipe, discard buffered frames and leave a guaranteed sentinel.
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill()
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            self.queue.put_nowait(None)

        if self._writer is not None:
            if abort:
                stop_writer()
            else:
                try:
                    self.queue.put(None, timeout=STALL_SECONDS)
                except queue.Full:
                    abort, error = True, "The frame writer stopped consuming frames."
                    stop_writer()
            self._writer.join(timeout=60)
            if self._writer.is_alive():
                abort, error = True, "The frame writer did not finish within 60 s."
                stop_writer()
                self._writer.join(timeout=5)
        returncode = None
        if self._proc is not None:
            if abort and self._proc.poll() is None:
                self._proc.kill()
            try:
                returncode = self._proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                returncode = self._proc.wait()
                error = "ffmpeg did not finish within 120 s."
            if self._drain is not None:
                self._drain.join(timeout=5)
        error = error or self._failed
        size_limited = not requested_abort and _size_limit_reached(self.partial)
        if abort or error or returncode != 0 or size_limited:
            self.partial.unlink(missing_ok=True)
            if requested_abort:
                return
            if size_limited:
                raise VideoSizeLimitError("Video reached its local MP4 size limit.")
            raise EncodeError(error or f"ffmpeg exit {returncode}: {tail(chr(10).join(self._stderr))}")


def mux_audio(ffmpeg: str, video: Path, audio_raw: Path, rate: int, target: Path) -> None:
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(video),
        "-f", "f32le", "-ar", str(rate), "-ac", "2", "-i", str(audio_raw),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", str(min(192_000, rate * 6)),
        "-movflags", "+faststart", "-threads", str(effective_threads()), "-fs", str(MP4_LIMIT), "-f", "mp4", str(target),
    ]  # fmt: skip
    result = run_tool(command, timeout=300)
    size_limited = _size_limit_reached(target)
    if result.returncode != 0 or size_limited:
        target.unlink(missing_ok=True)
        if size_limited:
            raise VideoSizeLimitError("Video with audio reached its local MP4 size limit.")
        raise EncodeError(f"Audio mux failed (exit {result.returncode}): {tail(result.stderr)}")


@dataclass(frozen=True, slots=True)
class VideoFacts:
    frames: int
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    audio_rate: int | None
    size_bytes: int


def _fraction(text) -> float:
    top, _, bottom = str(text).partition("/")
    try:
        return float(top) / float(bottom or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(ffprobe: str, path: Path) -> VideoFacts:
    """What the finished file really holds (counted frames), so metadata never repeats the request."""
    result = run_tool(
        [ffprobe, "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(path)],
        timeout=120,
    )
    if result.returncode != 0:
        raise EncodeError(f"ffprobe exit {result.returncode}: {tail(result.stderr)}")
    document = json.loads(result.stdout)
    streams = document.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise EncodeError("The finished file has no video stream.")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        duration = float(document.get("format", {}).get("duration") or video.get("duration") or 0)
        frames = int(video.get("nb_read_frames") or video.get("nb_frames") or 0)
    except ValueError as exc:
        raise EncodeError("ffprobe returned unreadable values.") from exc
    return VideoFacts(
        frames,
        int(video["width"]),
        int(video["height"]),
        _fraction(video.get("avg_frame_rate", "0")),
        duration,
        audio is not None,
        int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        path.stat().st_size,
    )
