"""Throwaway proof of concept (#521): one video generation on DT+ with a relaxed receiver, an MP4 and a log.

Usage (from the repository root, PAID DT+ generation on every call):
    uv run python tools/video_poc/video_poc.py -p "a red fox runs through snow"
    uv run python tools/video_poc/video_poc.py -p "..." -i start.png
    uv run python tools/video_poc/video_poc.py -p "..." -i a.png b.png c.png
    uv run python tools/video_poc/video_poc.py -p "..." -sz 1280x768

Exactly one generation per call. The request goes through ``build_generation_message`` (text-to-video config plus
``num_frames``); the response is read by this script's own receiver (no single-output rule, no PNG check, no size cap,
chunking tolerated). Frames stream to disk and into the installed ``ffmpeg`` (found on PATH); ``ffprobe`` checks the
result. Every run writes ``tmp/video-poc-YYYYMMDD-HHMMSS.log``. The API key comes from the same place as in the app
(saved key first, then DRAWTHINGS_API_KEY) and is never printed or logged.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import queue
import random
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

VERSION = "2"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "ltx_2.3_22b_distilled_1.1_q6p.ckpt"
HEADER_BYTES = 68
STALL_SECONDS = 90  # a frame writer that makes no progress for this long fails the run
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

# Draw Things ModelZoo tables (read in its source; the response carries neither value).
FPS_BY_FAMILY = {"ltx": 25, "wan": 16, "wan_v2.2": 24, "hunyuan": 30, "h3": 24}
DEFAULT_FPS = 30
AUDIO_RATE_BY_MODEL = (("ltx_2.3", 48000), ("ltx_2", 24000))
DEFAULT_AUDIO_RATE = 24000

EXIT_OK, EXIT_FAILED, EXIT_INVALID, EXIT_TOOL = 0, 1, 2, 3


# -- log -------------------------------------------------------------------------------------------------------------


class Log:
    """Plain UTF-8 log: one timestamped line per event, flushed after every line, secrets removed from every line."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w", encoding="utf-8", newline="\n")
        self.started = time.monotonic()
        self.secrets: set[str] = set()
        self.warnings: list[str] = []
        self.progress: Progress | None = None

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[key removed]")
        text = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [removed]", text)
        return re.sub(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+", r"\1[removed]", text)

    def line(self, text: str = "", *, echo: bool = False) -> None:
        text = self.redact(text)
        stamp = f"{datetime.now():%H:%M:%S.%f}"[:-3]
        for part in text.split("\n") or [""]:
            self._file.write(f"{stamp} +{self.elapsed():8.3f}s  {part}\n")
        self._file.flush()
        if echo:
            if self.progress is not None:
                self.progress.clear()
            print(text, flush=True)

    def section(self, title: str) -> None:
        self._file.write(f"\n=== {title} ===\n")
        self._file.flush()

    def warn(self, text: str) -> None:
        self.warnings.append(text)
        self.line("WARNING: " + text, echo=True)

    def close(self) -> None:
        self._file.close()


class Progress:
    """One updating console line while DT+ works (plain lines when stdout is not a terminal); the log keeps the detail.

    Shows seconds since the request was sent, the stage DT+ reports (waiting, text encoded, sampling step n of N,
    receiving frame k of F, finishing) and the compressed megabytes received once frames flow.
    """

    def __init__(self, enabled: bool, steps: int, frames: int):
        self.enabled, self.steps, self.frames = enabled, steps, frames
        self.tty = sys.stdout.isatty()
        self.stage = "waiting for DT+ (queued or starting)"
        self.received = 0
        self.nbytes = 0
        self.sent_at: float | None = None
        self.log: Log | None = None
        self._shown = 0
        self._last_plain = ("", -999.0)

    def start(self) -> None:
        self.sent_at = time.monotonic()
        self.render()

    def set(self, stage: str) -> None:
        self.stage = stage
        self.render()

    def text(self) -> str:
        elapsed = 0 if self.sent_at is None else int(time.monotonic() - self.sent_at)
        size = f" | {self.nbytes / 1_048_576:.1f} MB received" if self.nbytes else ""
        return f"[{elapsed:4d}s] {self.stage}{size}"

    def render(self) -> None:
        if not self.enabled or self.sent_at is None:
            return
        text = self.text()
        if self.tty:
            self._shown = max(self._shown, len(text))
            sys.stdout.write("\r" + text.ljust(self._shown))
            sys.stdout.flush()
            return
        elapsed = time.monotonic() - self.sent_at
        kind = re.sub(r"\d+", "#", self.stage)  # plain output: one line per kind of stage, plus one every 30 s
        if kind != self._last_plain[0] or elapsed - self._last_plain[1] >= 30:
            self._last_plain = (kind, elapsed)
            print(text, flush=True)

    def clear(self) -> None:
        if self.enabled and self.tty and self._shown:
            sys.stdout.write("\r" + " " * self._shown + "\r")
            sys.stdout.flush()

    def stop(self) -> None:
        self.clear()
        self.enabled = False

    async def tick(self) -> None:
        beats = 0
        while True:
            await asyncio.sleep(1)
            self.render()
            beats += 1
            if self.log is not None and beats % 30 == 0:  # the log shows life even while DT+ is silent
                self.log.line("  still running: " + self.text())


def quiet_process_kwargs() -> dict:
    """No console window for ffmpeg/ffprobe on Windows (also when the caller is a windowed app)."""
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
        **quiet_process_kwargs(),
    )


# -- arguments and small helpers -------------------------------------------------------------------------------------


def parse_size(text: str) -> tuple[int, int]:
    """``WIDTHxHEIGHT`` (width first) as typed; the values are adapted later by ``snap_side``."""
    match = re.fullmatch(r"(\d+)[xX](\d+)", text.strip())
    if not match or not int(match[1]) or not int(match[2]):
        raise argparse.ArgumentTypeError(f"-sz must look like WIDTHxHEIGHT, for example 320x640 (got {text!r})")
    return int(match[1]), int(match[2])


def snap_side(value: int) -> int:
    """Nearest multiple of 64 (ties go up), kept inside 128..2048."""
    return min(2048, max(128, (value + 32) // 64 * 64))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False
    )
    parser.add_argument("-m", dest="model", default=None, help="model file (default LTX-2.3 distilled)")
    parser.add_argument("-c", dest="configs", type=Path, default=None, help="Draw Things configuration JSON file")
    parser.add_argument("-n", dest="preset_name", default=None, help="one named configuration in -c (or bundled presets)")
    parser.add_argument("-p", dest="prompt", default="", help="prompt")
    parser.add_argument(
        "-sz",
        dest="size",
        type=parse_size,
        default=None,
        help="WIDTHxHEIGHT, e.g. 320x640; adapted to the nearest multiple of 64 in 128..2048 "
        "(default: 320x320 for LTX, the model preset's native size for other families)",
    )
    parser.add_argument(
        "-f", dest="frames", type=int, default=None, help="frame count (default: the model preset's, else 121)"
    )
    parser.add_argument("-s", dest="steps", type=int, default=None, help="steps (default: the model profile's)")
    parser.add_argument("-g", dest="guidance", type=float, default=None, help="guidance (default: the model profile's)")
    parser.add_argument("-sh", dest="shift", type=float, default=None, help="shift (default: the model profile's)")
    parser.add_argument("-sd", dest="seed", type=int, default=None, help="seed (default random)")
    parser.add_argument(
        "-i", dest="images", type=Path, nargs="+", default=[], help="start image(s); several = multi-frame"
    )
    parser.add_argument("-o", dest="out", type=Path, default=Path("tmp") / "video-poc" / stamp, help="output folder")
    parser.add_argument("-kf", dest="keep_frames", action="store_true", help="keep the PNG frames")
    parser.add_argument("-q", dest="quiet", action="store_true", help="no live progress line (the log is unchanged)")
    parser.add_argument("-nomp4", dest="no_mp4", action="store_true", help="skip the ffmpeg encode")
    parser.add_argument("-to", dest="timeout", type=float, default=900, help="total timeout in seconds (default 900)")
    parser.add_argument("-lg", dest="log", type=Path, default=Path("tmp") / f"video-poc-{stamp}.log", help="log file")
    args = parser.parse_args(argv)
    args.requested_size = args.size
    return args


def select_preset(args: argparse.Namespace) -> dict | None:
    """Read one named configuration, without running a sweep or sending a generation request."""
    if args.configs is not None and not args.preset_name:
        raise ValueError("-c requires -n to select exactly one preset (one paid request per run).")
    if not args.preset_name:
        return None
    source = args.configs or ROOT / "src/dtflow/resources/presets/draw_things_configs.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, list):
        raise ValueError(f"Expected a list of named Draw Things presets in {source}.")
    matches = [
        item["configuration"]
        for item in document
        if isinstance(item, dict)
        and item.get("name") == args.preset_name
        and isinstance(item.get("configuration"), dict)
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one preset named {args.preset_name!r} in {source}; found {len(matches)}.")
    return matches[0]


def preset_for(model: str) -> dict | None:
    """The bundled Draw Things preset configuration for this checkpoint (quantization and build tag ignored)."""
    from dtflow.core.model_profiles import model_key

    path = ROOT / "src" / "dtflow" / "resources" / "presets" / "draw_things_configs.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    wanted = {model_key(name) for name in profile_candidates(model)}

    def walk(node):
        if isinstance(node, list):
            for item in node:
                yield from walk(item)
        elif isinstance(node, dict):
            if isinstance(node.get("configuration"), dict):
                yield node["configuration"]
            else:
                for item in node.values():
                    yield from walk(item)

    return next((c for c in walk(document) if model_key(str(c.get("model", ""))) in wanted), None)


def resolve_defaults(args: argparse.Namespace, log: Log) -> None:
    """Fill frames and size the user left out (and log where each value came from); steps, guidance and shift follow
    from the model profile later."""
    preset = args.preset
    if args.preset_name:
        for attribute, key in (("steps", "steps"), ("guidance", "guidanceScale"), ("shift", "shift"), ("seed", "seed")):
            if getattr(args, attribute) is None and key in preset:
                setattr(args, attribute, preset[key])
    if args.frames is None:
        native = preset.get("numFrames") if preset else None
        args.frames = int(native) if native else 121
        log.line(f"  frames {args.frames} ({'model preset' if native else 'POC default, no preset found'})", echo=True)
    else:
        log.line(f"  frames {args.frames} (argument)")
    if args.size is None:
        if family_of(args.model) == "ltx":
            args.requested_size, source = (320, 320), "POC default for LTX (small first run)"
        elif preset and preset.get("width") and preset.get("height"):
            args.requested_size, source = (int(preset["width"]), int(preset["height"])), "model preset (native size)"
        else:
            args.requested_size, source = (320, 320), "POC fallback, no preset found"
    else:
        source = "argument"
    args.width, args.height = snap_side(args.requested_size[0]), snap_side(args.requested_size[1])
    args.size = (args.width, args.height)
    log.line(f"  size {args.width}x{args.height} ({source})", echo=source != "argument")
    if args.requested_size != args.size:
        log.line(
            f"  size adapted to multiples of 64: requested {args.requested_size[0]}x{args.requested_size[1]}, "
            f"using {args.width}x{args.height}",
            echo=True,
        )


def family_of(model: str) -> str:
    """Classify POC families without changing the production video-model gate."""
    name = model.lower()
    if name.startswith(("pika_h3", "fasth3", "minimax_h3")):
        return "h3"
    if name.startswith("wan_v2.2"):
        return "wan_v2.2"
    for family in ("ltx", "wan", "hunyuan"):
        if name.startswith(family):
            return family
    return "unknown"


def frame_rule(model: str, frames: int) -> str | None:
    """A warning when the count breaks the family's frame rule (the decode rounds it down), else None."""
    family = family_of(model)
    step = {"ltx": 8, "wan": 4, "wan_v2.2": 4, "hunyuan": 4}.get(family)
    if family == "h3" and frames != 1 and (frames - 5) % 17:
        return f"{frames} frames may break the H3 rule (1 or 17k+5; preset uses 124)"
    if step and (frames - 1) % step:
        return (
            f"{frames} frames breaks the {family.upper()} rule {step}k+1; expect {(frames - 1) // step * step + 1} back"
        )
    return None


def audio_rate_for(model: str) -> int:
    name = model.lower()
    return next((rate for prefix, rate in AUDIO_RATE_BY_MODEL if name.startswith(prefix)), DEFAULT_AUDIO_RATE)


def profile_candidates(model: str) -> list[str]:
    """Checkpoint names to look a profile up with: the file itself, then without a ``_1.1`` style build tag."""
    stripped = re.sub(r"_\d+\.\d+(?=_(?:q\d|f16|f32|bf16))", "", model)
    return [model] if stripped == model else [model, stripped]


def tail(text: str, count: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-count:])


# -- images and tensors ----------------------------------------------------------------------------------------------


def load_images(paths: list[Path], width: int, height: int, log: Log):
    """Each image resized (cover fit) to the request size, stacked in the given order: uint8 array [N, H, W, 3]."""
    import numpy as np
    from PIL import Image, ImageOps

    frames = []
    for path in paths:
        data = path.read_bytes()
        with Image.open(path) as image:
            size = image.size
            frames.append(np.asarray(ImageOps.fit(image.convert("RGB"), (width, height), Image.Resampling.LANCZOS)))
        log.line(f"  image {path}: {size[0]}x{size[1]} px, sha256 {hashlib.sha256(data).hexdigest()[:12]}")
    return np.stack(frames)


def image_tensor(frames) -> bytes:
    """CCV tensor as the production image encoder builds it (float16 in [-1, 1], NHWC with N first), N frames."""
    import numpy as np

    count, height, width, channels = frames.shape
    values = (frames.astype("<f2") / 255.0 * 2.0 - 1.0).astype("<f2")
    header = bytearray(HEADER_BYTES)
    struct.pack_into("<9I", header, 0, 0, 1, 2, 0x20000, 0, count, height, width, channels)
    return bytes(header) + np.ascontiguousarray(values).tobytes()


def tensor_shape(tensor: bytes) -> tuple[int, int, int, int]:
    """(N, H, W, C) from a CCV header (N is 0 for fpzip-compressed received tensors, which do not store it)."""
    values = struct.unpack_from("<17I", tensor)
    return values[5], values[6], values[7], values[8]


def decode_frame(tensor: bytes) -> tuple[int, int, bytes]:
    """One received frame tensor to packed RGB bytes (runs in a worker thread)."""
    import numpy as np
    from drawthings_py.image.image_buffer import ImageBuffer

    buffer = ImageBuffer.from_tensor(tensor)
    pixels = np.frombuffer(buffer.data, dtype=np.uint8).reshape(buffer.height, buffer.width, buffer.channels)
    return buffer.width, buffer.height, np.ascontiguousarray(pixels[:, :, :3]).tobytes()


# -- frame sink: PNG files and ffmpeg --------------------------------------------------------------------------------


@dataclass
class EncodeResult:
    command: str = ""
    returncode: int | None = None
    stderr_tail: str = ""
    video_partial: Path | None = None
    size: tuple[int, int] | None = None
    frames: int = 0
    error: str | None = None


class FrameSink:
    """Frames leave memory at once: a writer thread saves each PNG and feeds ffmpeg's stdin through a small queue.

    ffmpeg's stderr is drained in its own thread (an unread pipe freezes it at about 64 KB), a stalled writer is
    detected by a timed queue put, and a writer that does not finish after the end is killed with the process.
    """

    def __init__(self, out: Path, ffmpeg: str | None, fps: int, log: Log, keep_png: bool):
        self.out, self.ffmpeg, self.fps, self.log, self.keep_png = out, ffmpeg, fps, log, keep_png
        self.frames_dir = out / "frames"
        self.queue: queue.Queue = queue.Queue(maxsize=8)
        self.result = EncodeResult()
        self._proc: subprocess.Popen | None = None
        self._writer: threading.Thread | None = None
        self._drain: threading.Thread | None = None
        self._stderr: deque[str] = deque(maxlen=60)
        self._failed: str | None = None

    def _start(self, width: int, height: int) -> None:
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.result.size = (width, height)
        if self.ffmpeg:
            self.result.video_partial = self.out / "video.mp4.partial"
            bitrate = max(9_500_000, width * height * 5)
            command = [
                self.ffmpeg, "-hide_banner", "-nostdin", "-y",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(self.fps), "-i", "pipe:0",
                "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.1", "-pix_fmt", "yuv420p",
                "-b:v", str(bitrate), "-g", "30", "-bf", "2",
                "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                "-movflags", "+faststart", "-f", "mp4", str(self.result.video_partial),
            ]  # fmt: skip
            self.result.command = subprocess.list2cmdline(command)
            self.log.line(f"ffmpeg started: {self.result.command}")
            self._proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                **quiet_process_kwargs(),
            )
            self._drain = threading.Thread(target=self._drain_stderr, daemon=True)
            self._drain.start()
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._writer.start()

    def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for raw in iter(self._proc.stderr.readline, b""):
            self._stderr.append(raw.decode("utf-8", "replace").rstrip())

    def _write_loop(self) -> None:
        from PIL import Image

        stdin = self._proc.stdin if self._proc else None
        width, height = self.result.size or (0, 0)
        try:
            while (item := self.queue.get()) is not None:
                if self._failed:
                    continue
                index, rgb = item
                try:
                    Image.frombytes("RGB", (width, height), rgb).save(self.frames_dir / f"frame-{index:05d}.png")
                    if stdin is not None:
                        stdin.write(rgb)
                    self.result.frames += 1
                except Exception as exc:
                    self._failed = f"frame {index}: {type(exc).__name__}: {exc}"
        finally:
            if stdin is not None:
                try:
                    stdin.close()
                except OSError:
                    pass

    def put(self, index: int, width: int, height: int, rgb: bytes) -> None:
        """Called from a worker thread, in frame order."""
        if self._writer is None:
            self._start(width, height)
        elif (width, height) != self.result.size:
            raise RuntimeError(f"frame {index} is {width}x{height}, the first frame was {self.result.size}")
        if self._failed:
            raise RuntimeError("frame writer failed: " + self._failed)
        try:
            self.queue.put((index, rgb), timeout=STALL_SECONDS)
        except queue.Full:
            raise RuntimeError(f"frame writer made no progress for {STALL_SECONDS} s") from None

    def finish(self, *, abort: bool = False) -> EncodeResult:
        """Close the writer and ffmpeg; abort kills ffmpeg and removes the half-written file."""
        result = self.result
        if self._writer is not None:
            try:
                self.queue.put(None, timeout=STALL_SECONDS)
            except queue.Full:
                abort = True
            self._writer.join(timeout=60)
            if self._writer.is_alive():
                abort = True
                result.error = "frame writer did not finish within 60 s; killed"
        if self._proc is not None:
            if abort:
                self._proc.kill()
            try:
                result.returncode = self._proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                result.returncode = self._proc.wait()
                result.error = "ffmpeg did not finish within 120 s; killed"
            if self._drain is not None:
                self._drain.join(timeout=5)
            result.stderr_tail = "\n".join(list(self._stderr)[-15:])
        if self._failed and not result.error:
            result.error = self._failed
        if result.video_partial is not None and (abort or result.returncode != 0 or result.error):
            result.video_partial.unlink(missing_ok=True)
            result.video_partial = None
        return result


# -- request ---------------------------------------------------------------------------------------------------------


def patch_configuration(
    configuration: bytes, frames: int, shift: float | None, preset: dict | None = None
) -> bytes:
    """Apply a selected Draw Things video preset to the POC-only FlatBuffer; keep production wire unchanged."""
    import flatbuffers
    from drawthings_py.generated.dt_grpc.config_generated import GenerationConfiguration, GenerationConfigurationT, LoRAT

    table = GenerationConfigurationT.InitFromObj(GenerationConfiguration.GetRootAs(configuration, 0))
    table.numFrames = frames
    if shift is not None:
        table.shift = shift
    if preset is not None:
        if preset.get("controls"):
            raise ValueError("Video smoke POC does not support ControlNet inputs.")
        if preset.get("batchSize", 1) != 1 or preset.get("batchCount", 1) != 1:
            raise ValueError("Video smoke POC submits one generation; batch presets are unsupported.")
        for source, target in (
            ("sampler", "sampler"),
            ("refinerModel", "refinerModel"),
            ("refinerStart", "refinerStart"),
            ("shiftForAudio", "shiftForAudio"),
            ("fps", "fpsId"),
            ("guidingFrameNoise", "condAug"),
            ("startFrameGuidance", "startFrameCfg"),
            ("hiresFix", "hiresFix"),
            ("hiresFixStrength", "hiresFixStrength"),
            ("tiledDecoding", "tiledDecoding"),
            ("stochasticSamplingGamma", "stochasticSamplingGamma"),
            ("stage2Steps", "stage2Steps"),
            ("stage2Guidance", "stage2Cfg"),
            ("stage2Shift", "stage2Shift"),
            ("t5TextEncoder", "t5TextEncoder"),
            ("guidanceEmbed", "guidanceEmbed"),
            ("speedUpWithGuidanceEmbed", "speedUpWithGuidanceEmbed"),
            ("resolutionDependentShift", "resolutionDependentShift"),
            ("zeroNegativePrompt", "zeroNegativePrompt"),
            ("teaCache", "teaCache"),
            ("teaCacheStart", "teaCacheStart"),
            ("teaCacheEnd", "teaCacheEnd"),
            ("teaCacheThreshold", "teaCacheThreshold"),
            ("teaCacheMaxSkipSteps", "teaCacheMaxSkipSteps"),
            ("seedMode", "seedMode"),
            ("clipSkip", "clipSkip"),
        ):
            if source in preset and hasattr(table, target):
                setattr(table, target, preset[source])
        for source, target in (
            ("hiresFixWidth", "hiresFixStartWidth"),
            ("hiresFixHeight", "hiresFixStartHeight"),
            ("decodingTileWidth", "decodingTileWidth"),
            ("decodingTileHeight", "decodingTileHeight"),
            ("decodingTileOverlap", "decodingTileOverlap"),
        ):
            if source in preset:
                setattr(table, target, max(0, int(preset[source]) // 64))
        modes = {"all": 0, "base": 1, "refiner": 2}
        loras = preset.get("loras", [])
        if not isinstance(loras, list):
            raise ValueError("Preset loras must be a list.")
        table.loras = [
            LoRAT(file=item["file"], weight=float(item["weight"]), mode=modes[item.get("mode", "all").lower()])
            for item in loras
        ]
    builder = flatbuffers.Builder(0)
    builder.Finish(table.Pack(builder))
    return bytes(builder.Output())


def sent_configuration(configuration: bytes) -> dict:
    from drawthings_py.configs.gen_config import GenConfig

    return {key: value for key, value in GenConfig.from_fbs(configuration).items() if value is not None}


# -- receiver --------------------------------------------------------------------------------------------------------


@dataclass
class Received:
    download_size: int | None = None
    tensors: list[dict] = field(default_factory=list)
    audio: bytearray = field(default_factory=bytearray)
    audio_messages: int = 0
    messages: int = 0
    chunk_states: dict[str, int] = field(default_factory=dict)
    previews: int = 0
    tags: list[str] = field(default_factory=list)
    cu_hints: list[str] = field(default_factory=list)
    first_content: float | None = None
    last_frame: float | None = None
    sampling_steps: int = 0
    scale_factors: set = field(default_factory=set)
    remote_download: bool = False
    initial_metadata: list = field(default_factory=list)
    trailing_metadata: list = field(default_factory=list)


async def receive(stream, got: Received, sink: FrameSink, log: Log, request_sent: float, progress: Progress) -> None:
    import betterproto
    from drawthings_py.generated.dt_grpc import image_service

    pending = bytearray()
    async for response in stream:
        now = time.monotonic() - request_sent
        got.messages += 1
        state = response.chunk_state.name if hasattr(response.chunk_state, "name") else str(response.chunk_state)
        got.chunk_states[state] = got.chunk_states.get(state, 0) + 1
        parts = [f"msg {got.messages} +{now:.2f}s chunk={state}"]
        if response.download_size:
            progress.set(f"DT+ is sending frames ({int(response.download_size) / 1_048_576:.0f} MB in total)")
            got.download_size = int(response.download_size)
            parts.append(f"download_size={got.download_size}")
        if response.scale_factor:
            got.scale_factors.add(int(response.scale_factor))
        if response.preview_image:
            got.previews += 1
            parts.append(f"preview={len(response.preview_image)}B")
        if response.tags:
            got.tags.extend(str(tag) for tag in response.tags)
            parts.append(f"tags={list(response.tags)}")
        if response.remote_download:
            got.remote_download = True
            parts.append("remote_download=set")
        signpost = None
        if response.current_signpost:
            name, value = betterproto.which_one_of(response.current_signpost, "signpost")
            signpost = f"{name}:{getattr(value, 'step', '')}"
            if name == "sampling":
                got.sampling_steps = max(got.sampling_steps, int(value.step))
                progress.set(f"sampling step {int(value.step)} of {progress.steps}")
            elif name == "text_encoded":
                progress.set("text encoded")
            elif name == "image_decoded":
                progress.set("start image decoded")
        if signpost:
            parts.append(f"signpost={signpost}")
        if response.signposts:
            parts.append(
                "signposts=" + ",".join(betterproto.which_one_of(item, "signpost")[0] for item in response.signposts)
            )
        if response.generated_audio:
            chunk = sum(len(item) for item in response.generated_audio)
            for item in response.generated_audio:
                got.audio.extend(item)
            got.audio_messages += 1
            parts.append(f"audio={chunk}B")
        has_content = bool(response.generated_images or response.generated_audio or response.download_size)
        if has_content and got.first_content is None:
            got.first_content = now
            parts.append("FIRST CONTENT")
        for data in response.generated_images:
            pending.extend(data)
            if response.chunk_state == image_service.ChunkState.MORE_CHUNKS:
                parts.append(f"image piece {len(data)}B")
                continue
            tensor = bytes(pending)
            pending.clear()
            index = len(got.tensors) + 1
            _, height, width, channels = tensor_shape(tensor)
            raw = HEADER_BYTES + width * height * channels * 2
            got.tensors.append(
                {
                    "index": index,
                    "shape": (1, height, width, channels),
                    "raw": raw,
                    "compressed": len(tensor),
                    "chunk": state,
                }
            )
            parts.append(f"frame {index} {width}x{height}x{channels} {len(tensor)}B (raw {raw}B)")
            rgb_width, rgb_height, rgb = await asyncio.to_thread(decode_frame, tensor)
            await asyncio.to_thread(sink.put, index, rgb_width, rgb_height, rgb)
            progress.nbytes += len(tensor)
            progress.set(f"receiving frame {index} of {progress.frames}")
            got.last_frame = now
        log.line("  " + " ".join(parts))
    for attribute in ("initial_metadata", "trailing_metadata"):
        items = getattr(stream, attribute, None)
        if items:
            setattr(got, attribute, [(str(k), str(v)) for k, v in dict(items).items()])
    if pending:
        log.warn(f"{len(pending)} bytes of an unfinished chunked tensor were left at the end of the stream")


def cu_visibility(got: Received) -> str:
    """Billed CUs are shown only if some field plainly says so; otherwise the log states it is not visible."""
    hints = []
    for key, value in (*got.initial_metadata, *got.trailing_metadata):
        if re.search(r"(?i)\b(cu|credit|compute|cost|bill)", key):
            hints.append(f"{key}={value}")
    hints += [tag for tag in got.tags if re.search(r"(?i)\b(cu|credit|compute|cost|bill)", tag)]
    return ", ".join(hints) if hints else "not visible"


# -- ffprobe and audio -----------------------------------------------------------------------------------------------


def probe(ffprobe: str, path: Path) -> dict:
    command = [ffprobe, "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(path)]
    result = run_tool(command, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe exit {result.returncode}: {tail(result.stderr)}")
    return json.loads(result.stdout)


def number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fraction(text) -> float | None:
    try:
        top, _, bottom = str(text).partition("/")
        return float(top) / float(bottom or 1)
    except (ValueError, ZeroDivisionError):
        return None


def mux_audio(ffmpeg: str, video: Path, audio_raw: Path, rate: int, target: Path, log: Log) -> tuple[bool, str]:
    bitrate = min(192_000, rate * 6)
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(video),
        "-f", "f32le", "-ar", str(rate), "-ac", "2", "-i", str(audio_raw),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", str(bitrate),
        "-movflags", "+faststart", "-f", "mp4", str(target),
    ]  # fmt: skip
    log.line("audio mux: " + subprocess.list2cmdline(command))
    result = run_tool(command, timeout=300)
    log.line(f"audio mux exit code {result.returncode}")
    if result.stderr.strip():
        log.line("audio mux stderr tail:\n" + tail(result.stderr))
    return result.returncode == 0, result.stderr


# -- main run --------------------------------------------------------------------------------------------------------


@dataclass
class Findings:
    verdict: str = "run did not finish"
    ok: bool = False
    outputs: list[str] = field(default_factory=list)


def tool_versions(log: Log, ffmpeg: str | None, ffprobe: str | None) -> None:
    for label, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        if not path:
            log.line(f"{label}: NOT FOUND on PATH")
            continue
        try:
            first = run_tool([path, "-version"], timeout=20).stdout.splitlines()[0]
        except Exception as exc:
            first = f"version unreadable ({type(exc).__name__})"
        log.line(f"{label}: {path} | {first}")


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            **quiet_process_kwargs(),
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


async def generate(args, log: Log, found: Findings, ffmpeg: str | None, ffprobe: str | None) -> None:
    from dtflow.backends.drawthings.versions import bundled_base_version
    from dtflow.backends.drawthings.wire import build_generation_message
    from dtflow.composition import BackendSelection, create_backend
    from dtflow.core.model_profiles import profile_for
    from dtflow.core.models import BackendError, ResolvedGenerationRequest, TimeoutPolicy, ValidationError
    from dtflow.paths import application_state_dir
    from dtflow.services.credentials import (
        INDEX_FILE,
        EnvironmentCredentials,
        OptionalSavedCredentials,
        SavedCredentials,
        resolve_credentials,
    )
    from drawthings_py.generated.dt_grpc import image_service
    from grpclib.const import Cardinality

    family_fps = FPS_BY_FAMILY.get(family_of(args.model), DEFAULT_FPS)
    fps = int(args.preset.get("fps", family_fps)) if args.preset_name else family_fps
    encode = not args.no_mp4

    # Section 3: profile and request summary.
    log.section("3 Model profile and request")
    base_version = bundled_base_version(args.model)
    profile, profile_name = None, None
    for candidate in profile_candidates(args.model):  # exact entries first, the family default only as a last resort
        profile = profile_for(candidate)
        if profile:
            profile_name = candidate
            break
    if profile is None:
        profile, profile_name = profile_for(args.model, base_version), "family default"
    from dtflow.backends.drawthings.wire import SAMPLER_NAME, SAMPLER_WIRE_VALUE

    if args.preset_name:
        numeric_sampler = args.preset.get("sampler")
        sampler = next(
            (candidate for candidate, name in SAMPLER_NAME.items() if SAMPLER_WIRE_VALUE[name] == numeric_sampler),
            None,
        )
        if sampler is None:
            raise ValidationError(f"Unsupported preset sampler number: {numeric_sampler!r}.")
    elif profile is not None:
        sampler = profile.sampler
    else:
        raise ValidationError(f"No bundled profile for {args.model}; select a named preset with -n.")
    extras = dict(profile.extras) if profile is not None else {}
    if profile is None:
        log.line(f"no bundled profile: using selected preset {args.preset_name!r}, sampler {sampler.value}")
    else:
        why = (
            "its own entry"
            if profile_name == args.model
            else "the family default (no entry for this file)"
            if profile_name == "family default"
            else f"the entry of {profile_name} (build tag removed)"
        )
        log.line(
            f"profile picked: {profile.name} via {why}; sampler {sampler.value}, "
            f"profile steps {profile.steps}, guidance {profile.guidance}, extras {extras}; base family {base_version}",
            echo=True,
        )
    sources = []
    for name, wanted, own in (
        ("steps", args.steps, profile.steps if profile else None),
        ("guidance", args.guidance, profile.guidance if profile else None),
        ("shift", args.shift, extras.get("shift")),
    ):
        value = own if wanted is None else wanted
        setattr(args, name, None if value is None else (float(value) if name != "steps" else int(value)))
        source = "profile" if wanted is None else "preset" if args.preset_name and getattr(args, name) is not None else "argument"
        sources.append(f"{name} {value} ({source})")
    log.line("values used: " + ", ".join(sources), echo=True)
    seed = args.seed if args.seed is not None else random.randrange(0, 2**31)
    count = len(args.images)
    log.line(
        f"request: model {args.model}, frames {args.frames}, {args.width}x{args.height}, steps {args.steps}, "
        f"guidance {args.guidance}, shift {args.shift}, seed {seed}, start images {count} "
        f"({'none' if not count else 'one start frame' if count == 1 else f'multi-frame start of {count} frames'})"
    )
    log.line(f"playback fps assumed from the Draw Things model table: {fps} (the response carries none)")
    if args.preset_name:
        loras = [(item["file"], item.get("mode", "all"), item["weight"]) for item in args.preset.get("loras", [])]
        log.line(f"selected preset: {args.preset_name!r}; refiner {args.preset.get('refinerModel', 'none')}; "
                 f"LoRAs {loras}")
        if "shiftForAudio" in args.preset:
            log.warn("This SDK's generated FlatBuffer has no shiftForAudio field; provider default applies.")
    else:
        log.line("hires fix and start-frame strength fields are not sent (defaults)")
    if (warning := frame_rule(args.model, args.frames)) is not None:
        log.warn(warning)
    if "ref2va" in args.model.lower() and not count:
        log.warn("Ref2VA typically requires a reference image; no -i was supplied (video output unverified).")
    if count > args.frames:
        raise ValidationError(f"{count} start images but only {args.frames} frames requested.")
    start_frames = await asyncio.to_thread(load_images, args.images, args.width, args.height, log) if count else None
    if count:
        log.line("  images are cover-fitted (resized, centre-cropped) to the request size")

    request = ResolvedGenerationRequest(
        request_id=uuid.uuid4().hex,
        prompt=args.prompt,
        model_id=args.model,
        seed=seed,
        width_px=args.width,
        height_px=args.height,
        steps=args.steps,
        guidance=args.guidance,
        sampler_id=sampler,
    )

    # Credentials resolve here only to fail early and to keep the secret out of the log.
    saved = SavedCredentials()
    saved.attach(application_state_dir() / INDEX_FILE)
    optional = OptionalSavedCredentials(saved)
    try:
        credential = await resolve_credentials(EnvironmentCredentials(os.environ), optional)
    except BackendError:
        raise ValidationError("No DT+ key: save one in the app or set DRAWTHINGS_API_KEY.") from None
    log.secrets.add(credential.secret)
    log.line(f"DT+ key source: {credential.source}" + (f" (name {credential.alias})" if credential.alias else ""))
    backend = create_backend(
        BackendSelection("cloud"), timeouts=TimeoutPolicy(total=args.timeout), saved=optional, preview_images=False
    )

    sink = FrameSink(args.out, ffmpeg if encode else None, fps, log, args.keep_frames)
    got = Received()
    progress = Progress(not args.quiet, args.steps, args.frames)
    log.progress = progress
    progress.log = log
    ticker = None
    deadline = time.monotonic() + args.timeout
    request_sent = time.monotonic()
    try:
        async with asyncio.timeout(args.timeout):
            await backend.open()
            log.section("4 Wire details")
            connect_started = time.monotonic()
            await backend._connect()  # script-local use of the backend's own connect step
            log.line(f"timing connect: {time.monotonic() - connect_started:.2f}s to {backend.endpoint.label}")
            spec = await backend._model_spec(args.model)
            log.line(
                f"model description for DT+: advertised by Echo {args.model in backend._model_specs}, sent {bool(spec)}"
            )
            wire = build_generation_message(request, None, base_version, spec)
            message = wire.message
            message.configuration = patch_configuration(
                message.configuration, args.frames, args.shift, args.preset if args.preset_name else None
            )
            config = sent_configuration(message.configuration)
            log.line("GenConfig fields sent: " + json.dumps(config, sort_keys=True, default=str))
            if args.preset_name:
                from drawthings_py.generated.dt_grpc.config_generated import GenerationConfiguration

                raw = GenerationConfiguration.GetRootAs(message.configuration, 0)
                if raw.Sampler() != args.preset["sampler"]:
                    raise ValidationError("Serialized sampler differs from selected Draw Things preset.")
                if raw.LorasLength() != len(args.preset.get("loras", [])):
                    raise ValidationError("Serialized LoRA count differs from selected preset.")
                for index, item in enumerate(args.preset.get("loras", [])):
                    lora = raw.Loras(index)
                    modes = {"all": 0, "base": 1, "refiner": 2}
                    if (
                        lora.File().decode() != item["file"]
                        or lora.Mode() != modes[item.get("mode", "all").lower()]
                        or abs(lora.Weight() - float(item["weight"])) > 0.0001
                    ):
                        raise ValidationError(f"Serialized LoRA {index} differs from selected preset.")
                if args.preset.get("refinerModel") and raw.RefinerModel().decode() != args.preset["refinerModel"]:
                    raise ValidationError("Serialized refiner model differs from selected preset.")
                log.line(
                    f"preset wire fields: sampler={raw.Sampler()}, refiner={raw.RefinerModel()}, "
                    f"refinerStart={raw.RefinerStart()}, loras={raw.LorasLength()}, fpsId={raw.FpsId()}"
                )
            for key, wanted in (
                ("num_frames", args.frames),
                ("steps", args.steps),
                ("width", args.width),
                ("height", args.height),
            ):
                if config.get(key) != wanted:
                    raise ValidationError(f"Serialized {key} is {config.get(key)}, wanted {wanted}.")
            if start_frames is not None:
                tensor = image_tensor(start_frames)
                message.image = hashlib.sha256(tensor).digest()
                message.contents = [tensor]
                log.line(
                    f"image tensor shape {tuple(start_frames.shape)} float16, {len(tensor)} bytes "
                    "(sent uncompressed like the production encoder), referenced by sha256 in `image`"
                )
            else:
                log.line("no image tensor (text-to-video)")
            log.line(f"chunked flag in request: {message.chunked}; request message {len(bytes(message))} bytes")
            auth_started = time.monotonic()
            metadata = await backend._generation_metadata(message)
            log.line(f"timing authorize: {time.monotonic() - auth_started:.2f}s")

            log.section("5 Timings and 6 Receiver record (live)")
            channel = backend._require_channel()
            async with channel.request(
                "/ImageGenerationService/GenerateImage",
                Cardinality.UNARY_STREAM,
                image_service.ImageGenerationRequest,
                image_service.ImageGenerationResponse,
                timeout=max(1.0, deadline - time.monotonic()),
                metadata=metadata,
            ) as stream:
                await stream.send_message(message, end=True)
                request_sent = time.monotonic()
                log.line("timing request sent; waiting for DT+ (PAID generation is running)", echo=True)
                progress.start()
                ticker = asyncio.create_task(progress.tick())
                await receive(stream, got, sink, log, request_sent, progress)
    except BaseException as exc:
        if ticker:
            ticker.cancel()
        progress.stop()
        sink.finish(abort=True)
        await backend_close(backend)
        raise_with_record(exc, log, got, args, request_sent, deadline)
    if ticker:
        ticker.cancel()
    progress.set("finishing: encoding the MP4, audio mux, ffprobe")
    await backend_close(backend)
    total = time.monotonic() - request_sent
    log.section("5 Timings")
    log.line(
        f"connect/request sent/first content/last frame/total: first content "
        f"{'none' if got.first_content is None else f'{got.first_content:.2f}s'} after send, last frame "
        f"{'none' if got.last_frame is None else f'{got.last_frame:.2f}s'}, stream ended {total:.2f}s after send"
    )
    log.line(f"-to budget {args.timeout:.0f}s, left {max(0.0, deadline - time.monotonic()):.1f}s")
    try:
        await finish_run(args, log, found, got, sink, ffmpeg, ffprobe, fps, encode)
    finally:
        progress.stop()


async def backend_close(backend) -> None:
    try:
        await backend.aclose()
    except Exception:
        pass


def raise_with_record(exc: BaseException, log: Log, got: Received, args, request_sent: float, deadline: float) -> None:
    """Log what DT+ or the transport said (verbatim, key removed), the partial receiver record, then re-raise."""
    log.section("6 Receiver record (failed run)")
    log.line(
        f"failed {time.monotonic() - request_sent:.2f}s after send, budget left {max(0.0, deadline - time.monotonic()):.1f}s"
    )
    log.line(
        f"messages {got.messages}, tensors {len(got.tensors)}, audio {len(got.audio)} bytes, download_size {got.download_size}"
    )
    log.line(f"error type: {type(exc).__module__}.{type(exc).__name__}")
    failure = getattr(exc, "failure", None)
    if failure is not None:
        log.line(f"DT+/backend error: code={failure.code} category={failure.category} message={failure.message}")
        for key, value in failure.diagnostics:
            log.line(f"  diagnostic {key}={value}")
    for attribute in ("status", "message", "details"):
        if hasattr(exc, attribute):
            log.line(f"  {attribute}: {getattr(exc, attribute)!r}")
    log.line(f"error text: {exc}")
    raise exc


async def finish_run(
    args, log: Log, found: Findings, got: Received, sink: FrameSink, ffmpeg, ffprobe, fps: int, encode: bool
) -> None:
    import numpy as np

    log.section("6 Receiver record")
    log.line(f"downloadSize value: {got.download_size}")
    log.line(
        f"messages {got.messages}; chunk_state counts {got.chunk_states}; previews {got.previews}; scale_factor {sorted(got.scale_factors)}"
    )
    sizes = sorted({tuple(t["shape"]) for t in got.tensors})
    log.line(f"tensors received: {len(got.tensors)} (requested {args.frames}); distinct shapes (N,H,W,C): {sizes}")
    compressed = sum(t["compressed"] for t in got.tensors)
    raw = sum(t["raw"] for t in got.tensors)
    log.line(
        f"bytes: compressed total {compressed}, raw total {raw}; per tensor min/max compressed "
        f"{min((t['compressed'] for t in got.tensors), default=0)}/{max((t['compressed'] for t in got.tensors), default=0)}"
    )
    if got.download_size is not None and compressed:
        log.line(f"downloadSize versus received compressed total: {got.download_size} vs {compressed}")
    log.line(f"billed CUs: {cu_visibility(got)}")
    if got.initial_metadata or got.trailing_metadata:
        log.line(f"grpc metadata: initial {got.initial_metadata}, trailing {got.trailing_metadata}")
    returned = sizes[0][1:3] if sizes else None
    if returned:
        log.line(
            f"returned frame size {returned[1]}x{returned[0]} versus requested {args.width}x{args.height}"
            + ("" if returned == (args.height, args.width) else "  <-- DIFFERENT (hires fix or scale?)"),
            echo=True,
        )

    audio_samples = None
    audio_rate = None
    audio_raw_path = None
    if got.audio:
        try:
            from drawthings_py.grpc.audio import AudioBuffer

            buffer = AudioBuffer.from_tensor(bytes(got.audio))
            audio_samples = buffer.length
            peak = float(np.abs(buffer.data).max()) if buffer.length else 0.0
            log.line(
                f"audio tensor: {len(got.audio)} bytes in {got.audio_messages} message(s), {buffer.length} samples x 2 channels, peak {peak:.4f}"
            )
            audio_raw_path = args.out / "audio.f32"
            audio_raw_path.write_bytes(np.ascontiguousarray(buffer.data, dtype="<f4").tobytes())
        except Exception as exc:
            log.warn(f"audio present ({len(got.audio)} bytes) but unreadable: {type(exc).__name__}: {exc}")
    else:
        log.line("audio tensor: none received (normal for models without audio)", echo=True)

    frames_in = len(got.tensors)
    if not frames_in:
        found.verdict = "DT+ answered but sent no frames"
        log.line("no frames received; nothing to encode")
        sink.finish(abort=True)
        return
    duration = frames_in / fps
    if audio_samples:
        inferred = audio_samples / duration
        table = audio_rate_for(args.model)
        agree = abs(inferred - table) / table <= 0.03
        audio_rate = table if agree else round(inferred)
        log.line(
            f"audio rate: computed {inferred:.0f} Hz from {audio_samples} samples / {duration:.3f}s; table {table} Hz; "
            f"{'agree within 3%, table used' if agree else 'DISAGREE, computed rate used'} -> {audio_rate} Hz",
            echo=True,
        )

    result = sink.finish()
    log.section("7 Encode")
    if not encode:
        log.line(f"-nomp4: no encode; PNG frames kept in {sink.frames_dir} ({result.frames} written)")
        found.ok = result.error is None and frames_in == args.frames
        found.outputs.append(str(sink.frames_dir))
        found.verdict = f"{frames_in}/{args.frames} frames received at {returned[1]}x{returned[0]}, PNG only (-nomp4)"
        if frames_in != args.frames:
            found.verdict += "; incomplete video, NOT a successful generation"
        return
    log.line(f"ffmpeg command: {result.command}")
    log.line(f"ffmpeg exit code: {result.returncode}; frames written {result.frames}")
    if result.stderr_tail:
        log.line("ffmpeg stderr tail:\n" + result.stderr_tail)
    if result.error or result.returncode != 0 or result.video_partial is None:
        found.verdict = f"{frames_in} frames received but the encode failed: {result.error or 'ffmpeg exit ' + str(result.returncode)}"
        found.outputs.append(str(sink.frames_dir))
        log.line("encode failed; the PNG frames are kept for inspection (nothing half-written is published)")
        return
    final = args.out / "video.mp4"
    if audio_raw_path is not None and audio_rate:
        muxed = args.out / "video.mux.mp4.partial"
        ok, _ = await asyncio.to_thread(mux_audio, ffmpeg, result.video_partial, audio_raw_path, audio_rate, muxed, log)
        if ok:
            result.video_partial.unlink(missing_ok=True)
            os.replace(muxed, final)
            log.line(f".partial publish: audio mux -> {final}")
        else:
            muxed.unlink(missing_ok=True)
            os.replace(result.video_partial, final)
            log.warn("audio mux failed; the MP4 was published without audio")
        if not args.keep_frames:
            audio_raw_path.unlink(missing_ok=True)
    else:
        os.replace(result.video_partial, final)
        log.line(f".partial publish: {final}")
    found.outputs.append(str(final))
    if not args.keep_frames:
        shutil.rmtree(sink.frames_dir, ignore_errors=True)
        log.line("PNG frames removed (use -kf to keep them)")
    else:
        found.outputs.append(str(sink.frames_dir))

    log.section("8 ffprobe versus request")
    try:
        data = await asyncio.to_thread(probe, ffprobe, final)
    except Exception as exc:
        log.warn(f"ffprobe failed: {exc}")
        found.verdict = f"{frames_in}/{args.frames} frames received and MP4 written, but ffprobe failed"
        found.ok = frames_in == args.frames and result.frames == args.frames
        if not found.ok:
            found.verdict += "; incomplete video, NOT a successful generation"
        return
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    real_frames = int(number(video.get("nb_read_frames")) or 0)
    real_fps = fraction(video.get("avg_frame_rate")) or fraction(video.get("r_frame_rate"))
    stream_duration = number(video.get("duration"))
    format_duration = number(data.get("format", {}).get("duration"))
    real_duration = stream_duration if stream_duration is not None else format_duration
    size = (video.get("width"), video.get("height"))
    log.line(
        f"ffprobe video: codec {video.get('codec_name')}, {size[0]}x{size[1]}, frames {real_frames}, fps {real_fps}, "
        f"duration stream {stream_duration} / container {format_duration}, size {data.get('format', {}).get('size')} bytes"
    )
    if audio:
        log.line(
            f"ffprobe audio: codec {audio.get('codec_name')}, {audio.get('sample_rate')} Hz, {audio.get('channels')} ch, duration {audio.get('duration')}"
        )
    differences = []
    if real_frames != args.frames:
        differences.append(f"frames: requested {args.frames}, tensors received {frames_in}, in MP4 {real_frames}")
    elif frames_in != args.frames:
        differences.append(f"frames: requested {args.frames}, received {frames_in}")
    if real_fps is not None and abs(real_fps - fps) > 0.01:
        differences.append(f"fps: assumed {fps}, MP4 {real_fps}")
    if size != (args.width, args.height):
        differences.append(f"size: requested {args.width}x{args.height}, MP4 {size[0]}x{size[1]}")
    if real_duration is not None and abs(real_duration - duration) > 0.2:
        differences.append(f"duration: expected {duration:.2f}s, MP4 {real_duration:.2f}s")
    if audio_samples and not audio:
        differences.append("audio received but the MP4 has no audio stream")
    log.line("differences from the request: " + ("; ".join(differences) if differences else "none"), echo=True)
    for item in differences:
        log.line("  DIFF " + item)
    found.ok = frames_in == args.frames and result.frames == args.frames and real_frames == args.frames
    audio_text = f", audio {audio_rate / 1000:g} kHz" if audio_samples and audio else ", no audio"
    found.verdict = f"{frames_in}/{args.frames} frames returned at {returned[1]}x{returned[0]}, {fps} fps{audio_text}, MP4 OK" + (
        f" ({len(differences)} difference(s) from the request)" if differences else ""
    )
    if not found.ok:
        found.verdict += "; incomplete video, NOT a successful generation"
    if count_images := len(args.images):
        found.verdict += f"; start image(s) {count_images}: DT+ accepted them, compare the first frames by eye"


def main() -> int:
    args = parse_args()
    log = Log(args.log)
    found = Findings()
    code = EXIT_FAILED
    print(f"log: {log.path}", flush=True)
    try:
        log.section("1 Header")
        log.line(f"video_poc script version {VERSION}, git commit {git_commit()}")
        log.line(f"python {platform.python_version()} on {platform.platform()}; cwd {Path.cwd()}")
        log.line(f"started {datetime.now().isoformat(timespec='seconds')}")
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        tool_versions(log, ffmpeg, ffprobe)
        log.section("2 Resolved arguments")
        for name, value in sorted(vars(args).items()):
            if name != "preset":
                log.line(f"  {name} = {value}")
        log.line("defaults resolved:")
        args.preset = select_preset(args)
        if args.preset_name:
            selected_model = args.preset.get("model")
            if not isinstance(selected_model, str) or not selected_model:
                raise ValueError("Selected preset is missing its model checkpoint.")
            if args.model is not None and args.model != selected_model:
                log.line(f"checkpoint override: preset model {selected_model} -> -m {args.model}", echo=True)
            else:
                args.model = selected_model
        elif args.model is None:
            args.model = DEFAULT_MODEL
        if family_of(args.model) == "unknown":
            raise ValueError(f"Not a recognized video checkpoint: {args.model}.")
        if args.preset is None:
            args.preset = preset_for(args.model) or {}
        resolve_defaults(args, log)
        for path in args.images:
            if not path.is_file():
                raise FileNotFoundError(f"start image not found: {path}")
        needed = [] if args.no_mp4 else [("ffmpeg", ffmpeg), ("ffprobe", ffprobe)]
        for name, path in needed:
            if not path:
                message = f"{name} not found on PATH: install it or add it to PATH"
                log.line(message)
                found.verdict = message
                print(message, file=sys.stderr, flush=True)
                code = EXIT_TOOL
                return code
        args.out.mkdir(parents=True, exist_ok=True)
        asyncio.run(generate(args, log, found, ffmpeg, ffprobe))
        code = EXIT_OK if found.ok else EXIT_FAILED
    except KeyboardInterrupt:
        found.verdict = "interrupted by the user"
        log.line("interrupted (Ctrl+C)")
    except (FileNotFoundError, ValueError) as exc:  # includes dtflow.core.models.ValidationError
        found.verdict = f"invalid input, nothing was sent: {exc}"
        log.line(found.verdict, echo=True)
        code = EXIT_INVALID
    except BaseException as exc:
        found.verdict = f"DT+ run failed: {type(exc).__name__}: {exc}"
        if not hasattr(exc, "failure"):
            log.line("unexpected exception:\n" + traceback.format_exc())
        else:
            log.line(found.verdict)
    finally:
        log.section("9 Verdict")
        log.line(f"VERDICT: {found.verdict}", echo=True)
        for item in log.warnings:
            log.line(f"warning: {item}")
        for item in found.outputs:
            log.line(f"output: {item}", echo=True)
        log.line(f"exit code {code}; total {log.elapsed():.1f}s")
        print(f"log: {log.path}", flush=True)
        log.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
