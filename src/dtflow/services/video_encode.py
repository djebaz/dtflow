"""ffmpeg and ffprobe work for video generation: frame sink, audio mux, probe, tool discovery.

Proven by the video POC on DT+ (libx264 high 4.1, yuv420p, faststart, AAC mux). Everything here blocks, so callers run
it in worker threads. The tools are the ones installed on the machine (``PATH``); how DTFlow ships them is a later
packaging decision. No console window opens on Windows.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
STALL_SECONDS = 90  # a frame writer that makes no progress for this long fails the run


def find_tools() -> tuple[str, str] | None:
    """``(ffmpeg, ffprobe)`` from PATH, or None when either is missing."""
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    return (ffmpeg, ffprobe) if ffmpeg and ffprobe else None


def tools_problem() -> str | None:
    if find_tools() is None:
        return "ffmpeg and ffprobe were not found on PATH; install them before generating video."
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


class FrameSink:
    """Frames leave memory at once: a writer thread feeds ffmpeg's stdin through a small queue.

    ffmpeg's stderr is drained in its own thread (an unread pipe freezes it at about 64 KB), a stalled writer is
    detected by a timed queue put, and a writer that does not finish after the end is killed with the process.
    """

    def __init__(self, ffmpeg: str, fps: int, partial: Path):
        self.ffmpeg, self.fps, self.partial = ffmpeg, fps, partial
        self.queue: queue.Queue = queue.Queue(maxsize=8)
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
            "-b:v", str(bitrate), "-g", "30", "-bf", "2",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-movflags", "+faststart", "-f", "mp4", str(self.partial),
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
            raise EncodeError("The frame writer failed: " + self._failed)
        try:
            self.queue.put(rgb, timeout=STALL_SECONDS)
        except queue.Full:
            raise EncodeError(f"The frame writer made no progress for {STALL_SECONDS} s.") from None

    def finish(self, *, abort: bool = False) -> None:
        """Close the writer and ffmpeg; abort (or any failure) kills ffmpeg and removes the half-written file."""
        if self._done:
            return
        self._done = True
        error = None
        if self._writer is not None:
            try:
                self.queue.put(None, timeout=STALL_SECONDS)
            except queue.Full:
                abort = True
            self._writer.join(timeout=60)
            if self._writer.is_alive():
                abort, error = True, "The frame writer did not finish within 60 s."
        returncode = None
        if self._proc is not None:
            if abort:
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
        if abort or error or returncode != 0:
            self.partial.unlink(missing_ok=True)
            if abort:
                return
            raise EncodeError(error or f"ffmpeg exit {returncode}: {tail(chr(10).join(self._stderr))}")


def mux_audio(ffmpeg: str, video: Path, audio_raw: Path, rate: int, target: Path) -> None:
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(video),
        "-f", "f32le", "-ar", str(rate), "-ac", "2", "-i", str(audio_raw),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", str(min(192_000, rate * 6)),
        "-movflags", "+faststart", "-f", "mp4", str(target),
    ]  # fmt: skip
    result = run_tool(command, timeout=300)
    if result.returncode != 0:
        target.unlink(missing_ok=True)
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
