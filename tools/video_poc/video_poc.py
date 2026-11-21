"""Throwaway proof of concept (#521): one video generation on DT+ with a relaxed receiver, an MP4 and a log.

Usage (from the repository root, PAID DT+ generation on every call):
    uv run python tools/video_poc/video_poc.py -p "a red fox runs through snow"
    uv run python tools/video_poc/video_poc.py -p "..." -i start.png
    uv run python tools/video_poc/video_poc.py -p "..." -i a.png b.png c.png
    uv run python tools/video_poc/video_poc.py -p "..." -sz 1280x768
    uv run python tools/video_poc/video_poc.py -p "..." -sz 1280x768 --hires-fix 2

Exactly one generation per call. The request goes through ``build_generation_message`` (text-to-video config plus
``num_frames``); the response is read by this script's own receiver (no single-output rule, no PNG check, no size cap,
chunking tolerated). Frames stream directly into installed ``ffmpeg`` (found on PATH), optionally saving still images; ``ffprobe`` checks the
result. Every run writes ``tmp/video-poc-YYYYMMDD-HHMMSS.log``. The API key comes from the same place as in the app
(saved key first, then DRAWTHINGS_API_KEY) and is never printed or logged.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
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
PIKA_MODEL = "pika_h3_flex_ref2va_i8x.ckpt"
MINIMAX_H3_MODEL = "minimax_h3_ref2va_i4x.ckpt"
H3_REF2VA_MODELS = (PIKA_MODEL, MINIMAX_H3_MODEL)
HEADER_BYTES = 68
STALL_SECONDS = 90  # a frame writer that makes no progress for this long fails the run
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

# Draw Things ModelZoo tables (read in its source; the response carries neither value).
FPS_BY_FAMILY = {"ltx": 25, "wan": 16, "hunyuan": 30, "h3": 24}
DEFAULT_FPS = 30
AUDIO_RATE_BY_MODEL = (("ltx_2.3", 48000), ("ltx_2", 24000), ("minimax_h3_", 32000), ("pika_h3_", 32000))
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

    Shows elapsed time, DT+ stage signposts (including separately reported Hires Fix passes), received frames
    and compressed megabytes. DT+ does not advertise a step total for each individual Hires Fix pass.
    """

    def __init__(self, enabled: bool, steps: int, frames: int, *, hires_requested: bool = False):
        self.enabled, self.steps, self.frames = enabled, steps, frames
        self.hires_requested = hires_requested
        self.second_pass_seen = False
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


def parse_size(text: str) -> tuple[int | None, int | None]:
    """Parse WIDTHxHEIGHT, auto, WIDTHxauto or autoxHEIGHT (width first)."""
    value = text.strip().lower()
    if value == "auto":
        return None, None
    match = re.fullmatch(r"(auto|\d+)x(auto|\d+)", value)
    if not match:
        raise argparse.ArgumentTypeError(f"-sz expects WIDTHxHEIGHT, auto, WIDTHxauto or autoxHEIGHT (got {text!r})")
    sides = tuple(None if side == "auto" else int(side) for side in match.groups())
    if 0 in sides:
        raise argparse.ArgumentTypeError("-sz dimensions must be positive.")
    return sides


def snap_side(value: int) -> int:
    """Nearest multiple of 64 (ties go up), kept inside 128..2048."""
    return min(2048, max(128, (value + 32) // 64 * 64))


def match_image_ratio(image_size: tuple[int, int], budget_size: tuple[int, int]) -> tuple[int, int]:
    """Approximate the first reference image ratio without materially changing the default pixel budget."""
    aspect = image_size[0] / image_size[1]
    pixels = budget_size[0] * budget_size[1]
    width = math.sqrt(pixels * aspect)
    height = math.sqrt(pixels / aspect)
    widths = {snap_side(round(width) + offset) for offset in (-64, 0, 64)}
    heights = {snap_side(round(height) + offset) for offset in (-64, 0, 64)}
    return min(
        ((w, h) for w in widths for h in heights),
        key=lambda size: (
            abs(math.log((size[0] / size[1]) / aspect))
            + 0.35 * abs(math.log((size[0] * size[1]) / pixels))
        ),
    )


def parse_config_value(raw: str):
    """Accept JSON literals, including true/false and LoRA/control arrays, or an unquoted string."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def parse_config_field(text: str) -> tuple[str, object]:
    """Parse a Draw Things field=value override without silently converting invalid values."""
    key, separator, raw = text.partition("=")
    if not separator or not key.strip() or not raw.strip():
        raise argparse.ArgumentTypeError("--set needs FIELD=VALUE (e.g. teaCache=true).")
    return key.strip(), parse_config_value(raw)


class ConfigOptionAction(argparse.Action):
    """Append a named Draw Things field in command-line order, alongside --set overrides."""

    def __init__(self, option_strings, dest, *, field_name: str, **kwargs):
        self.field_name = field_name
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        fields = getattr(namespace, self.dest, None)
        if fields is None:
            fields = []
        fields.append((self.field_name, parse_config_value(values)))
        setattr(namespace, self.dest, fields)


def add_drawthings_arguments(parser: argparse.ArgumentParser) -> None:
    """Register every serializable SDK configuration field not already owned by a dedicated CLI flag."""
    from drawthings_py.configs.gen_config import CONFIG_PROPS
    from drawthings_py.configs.prop_schema import load_definitions
    from drawthings_py.generated.dt_grpc.config_generated import GenerationConfigurationT

    reserved = {"model", "width", "height", "num_frames", "steps", "guidance", "shift", "seed", "sampler", "hires_fix"}
    native = GenerationConfigurationT()
    descriptions = load_definitions()
    group = parser.add_argument_group(
        "Draw Things configuration (advanced; effective support is model/provider-specific)"
    )
    for field_name, prop in sorted(CONFIG_PROPS.items()):
        if field_name in reserved or not hasattr(native, prop.config_t_name):
            continue
        options = ["--" + field_name.replace("_", "-"), "--" + field_name]
        if prop.json_name not in options:
            options.append("--" + prop.json_name)
        is_bool = type(getattr(native, prop.config_t_name)) is bool
        detail = descriptions.get(field_name, {}).get("description", "Draw Things setting")
        if field_name == "fps":
            detail += "; does not change the POC MP4 playback rate"
        elif field_name in ("loras", "controls"):
            detail += "; pass a JSON array of model objects (no automatic cloud asset upload)"
        elif field_name in ("hires_fix_width", "hires_fix_height", "decoding_tile_width", "decoding_tile_height",
                            "decoding_tile_overlap", "diffusion_tile_width", "diffusion_tile_height",
                            "diffusion_tile_overlap"):
            detail += "; pixel value must be a multiple of 64"
        elif field_name == "compression_artifacts":
            detail += "; values: Disabled, H264, H265, Jpeg (not the MP4 encoder)"
        elif field_name == "seed_mode":
            detail += "; values: Legacy, TorchCpuCompatible, ScaleAlike, NvidiaGpuCompatible"
        elif field_name == "face_restoration":
            detail += "; expects a model identifier, not a boolean"
        if is_bool:
            detail += "; omit VALUE for true or pass true/false"
        group.add_argument(
            *dict.fromkeys(options), dest="config_fields", action=ConfigOptionAction,
            field_name=field_name, default=argparse.SUPPRESS, metavar="VALUE",
            **({"nargs": "?", "const": "true"} if is_bool else {}),
            help=detail,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from dtflow.core.models import Sampler

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False
    )
    parser.add_argument("-m", dest="model", default=DEFAULT_MODEL, help="model file (default %(default)s)")
    parser.add_argument(
        "-spec", dest="model_specs", type=Path, default=None,
        help="JSON array of Draw Things model descriptions (exact checkpoint match, e.g. custom.json)",
    )
    parser.add_argument("-p", dest="prompt", default="", help="prompt")
    parser.add_argument(
        "-sz",
        dest="size",
        type=parse_size,
        default=None,
        help="FINAL output WIDTHxHEIGHT, auto, WIDTHxauto, or autoxHEIGHT (also with Hires Fix). "
        "Sides snap to 64px in 128..2048. With -i and no -sz, use the image ratio "
        "at approximately the model's default pixel budget.",
    )
    parser.add_argument(
        "-f", dest="frames", type=int, default=None, help="frame count (default: the model preset's, else 121)"
    )
    parser.add_argument("-s", dest="steps", type=int, default=None, help="steps (default: the model profile's)")
    parser.add_argument("-g", dest="guidance", type=float, default=None, help="guidance (default: the model profile's)")
    parser.add_argument("-sh", dest="shift", type=float, default=None, help="shift (default: the model profile's)")
    parser.add_argument(
        "-sampler", "--sampler", choices=[sampler.value for sampler in Sampler], default=None,
        help="sampling algorithm (default: model profile; TCD Trailing for H3 smoke)",
    )
    parser.add_argument("-neg", "--negative", default="", help="negative prompt")
    parser.add_argument(
        "-set", "--set", dest="config_fields", type=parse_config_field, action="append", default=[],
        metavar="FIELD=VALUE",
        help="override a supported SDK field (repeatable); accepts camelCase, snake_case or kebab-case "
        "names, JSON scalars, and JSON arrays for LoRAs/controls. Dedicated flags still own model, sampler, "
        "steps, guidance, shift, size, frames and seed.",
    )
    parser.add_argument(
        "--hires-fix", "--hires_fix", "--hiresFix", dest="hires_factor",
        type=float, nargs="?", const=2.0, default=None, metavar="FACTOR",
        help="upscale factor: 1 disables; 2 (default when flag used) to 4. -sz is the final resolution; "
        "the first pass is derived at multiples of 64, or overridden by --hires-fix-width/height.",
    )
    parser.add_argument("-sd", dest="seed", type=int, default=None, help="seed (default random)")
    parser.add_argument(
        "-i", dest="images", type=Path, nargs="+", default=[], help="start image(s); several = multi-frame"
    )
    parser.add_argument("-o", dest="out", type=Path, default=Path("tmp") / "video-poc" / stamp, help="output folder")
    parser.add_argument("-kf", dest="keep_frames", action="store_true", help="save frame images alongside MP4 (PNG by default)")
    parser.add_argument(
        "-jpg", dest="jpg_frames", action="store_true",
        help="save frame images as JPEG at quality 80 instead of PNG; implies -kf",
    )
    parser.add_argument("-q", dest="quiet", action="store_true", help="no live progress line (the log is unchanged)")
    parser.add_argument("-nomp4", dest="no_mp4", action="store_true", help="skip MP4; save PNG frames (JPEG with -jpg)")
    parser.add_argument("-to", dest="timeout", type=float, default=900, help="total timeout in seconds (default 900)")
    parser.add_argument("-lg", dest="log", type=Path, default=Path("tmp") / f"video-poc-{stamp}.log", help="log file")
    add_drawthings_arguments(parser)
    args = parser.parse_args(argv)
    args.requested_size = args.size
    return args


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
    """Resolve the video frame count and an image-aware output size before any paid request."""
    from PIL import Image, ImageOps

    preset = preset_for(args.model)
    if args.frames is None:
        native = preset.get("numFrames") if preset else None
        args.frames = 22 if args.model in H3_REF2VA_MODELS else int(native) if native else 121
        source = (
            "H3 Ref2VA smoke default"
            if args.model in H3_REF2VA_MODELS
            else "model preset" if native else "POC default, no preset found"
        )
        log.line(f"  frames {args.frames} ({source})", echo=True)
    else:
        log.line(f"  frames {args.frames} (argument)")
    if args.frames < 1:
        raise ValueError("-f must be positive.")
    if family_of(args.model) == "ltx" and (args.frames - 1) % 8:
        adjusted = (args.frames - 1) // 8 * 8 + 1
        log.warn(f"LTX requires 8k+1 frames: requested {args.frames}, using {adjusted} before submission")
        args.frames = adjusted

    if args.model == PIKA_MODEL:
        baseline, baseline_source = (768, 512), "Pika Ref2VA smoke default"
    elif args.model == MINIMAX_H3_MODEL:
        baseline, baseline_source = (256, 384), "MiniMax H3 Ref2VA iOS baseline"
    elif family_of(args.model) == "ltx":
        baseline, baseline_source = (320, 320), "POC default for LTX (small first run)"
    elif preset and preset.get("width") and preset.get("height"):
        baseline, baseline_source = (int(preset["width"]), int(preset["height"])), "model preset (native size)"
    else:
        baseline, baseline_source = (320, 320), "POC fallback, no preset found"

    image_size = None
    if args.images:
        with Image.open(args.images[0]) as image:
            image_size = ImageOps.exif_transpose(image).size
        if 0 in image_size:
            raise ValueError(f"First reference image has invalid dimensions: {image_size!r}")
        log.line(f"  first reference image size {image_size[0]}x{image_size[1]}", echo=True)

    specified = args.size
    if specified is None:
        if image_size:
            requested = match_image_ratio(image_size, baseline)
            source = f"reference image aspect ratio, near {baseline[0]}x{baseline[1]} pixel budget"
        else:
            requested, source = baseline, baseline_source
    elif specified == (None, None):
        if not image_size:
            raise ValueError("-sz auto needs at least one -i reference image.")
        requested, source = image_size, "reference image native size (-sz auto)"
    elif None in specified:
        if not image_size:
            raise ValueError("-sz WIDTHxauto or autoxHEIGHT needs at least one -i reference image.")
        aspect = image_size[0] / image_size[1]
        width, height = specified
        requested = (width, round(width / aspect)) if width is not None else (round(height * aspect), height)
        source = "explicit side, reference image aspect ratio"
    else:
        requested, source = specified, "explicit dimensions"

    args.requested_size = requested
    args.width, args.height = snap_side(requested[0]), snap_side(requested[1])
    args.size = (args.width, args.height)
    log.line(f"  size {args.width}x{args.height} ({source})", echo=True)
    if requested != args.size:
        log.line(
            f"  size adapted to multiples of 64: requested {requested[0]}x{requested[1]}, "
            f"using {args.width}x{args.height}", echo=True,
        )
    resolve_hires_fix(args, log)


def resolve_hires_fix(args: argparse.Namespace, log: Log) -> None:
    """Derive Hires Fix first-pass dimensions from the final -sz, preserving explicit overrides."""
    def normalized(name: str) -> str:
        return re.sub(r"[-_ ]", "", name).lower()

    fields = []
    legacy = []
    dimensions = {}
    for name, value in args.config_fields:
        field = normalized(name)
        if field == "hiresfix":
            legacy.append(value)
            continue
        if field in ("hiresfixwidth", "hiresfixstartwidth"):
            dimensions["width"] = (value, field == "hiresfixstartwidth")
        elif field in ("hiresfixheight", "hiresfixstartheight"):
            dimensions["height"] = (value, field == "hiresfixstartheight")
        fields.append((name, value))

    factor = args.hires_factor
    factor_explicit = factor is not None
    if legacy:
        if factor is not None:
            raise ValueError("Use either --hires-fix FACTOR or --set hiresFix, not both.")
        value = legacy[-1]
        if type(value) is bool:
            factor = 2.0 if value else 1.0
        elif type(value) in (int, float):
            factor = float(value)
        else:
            raise ValueError("--set hiresFix expects a boolean or factor from 1 to 4.")
    factor = 1.0 if factor is None else factor
    if not math.isfinite(factor) or not 1.0 <= factor <= 4.0:
        raise ValueError("--hires-fix factor must be between 1 (disabled) and 4.")
    args.hires_factor = factor
    if factor == 1.0:
        if dimensions:
            raise ValueError("--hires-fix-width/height requires --hires-fix with a factor greater than 1.")
        if factor_explicit or legacy:
            fields.append(("hires_fix", False))
        log.line("  Hires Fix: disabled (factor 1)", echo=True)
        args.config_fields = fields
        return

    first_pass = {}
    for axis, final in (("width", args.width), ("height", args.height)):
        explicit = dimensions.get(axis)
        if explicit is not None:
            value, is_native_units = explicit
            if type(value) is not int:
                raise ValueError(f"--hires-fix-{axis} expects an integer.")
            size = value * 64 if is_native_units else value
            if size % 64:
                raise ValueError(f"--hires-fix-{axis} must be a multiple of 64 pixels.")
        else:
            size = (round(final / factor) + 32) // 64 * 64
            fields.append((f"hires_fix_{axis}", size))
        if size < 128 or size >= final:
            raise ValueError(
                f"Hires Fix first-pass {axis} {size}px must be at least 128px and smaller than "
                f"final {axis} {final}px. Reduce --hires-fix or increase -sz."
            )
        first_pass[axis] = size

    fields.append(("hires_fix", True))
    args.config_fields = fields
    log.line(
        f"  Hires Fix: {factor:g}x, first pass {first_pass['width']}x{first_pass['height']} "
        f"-> final {args.width}x{args.height}"
        + (" (explicit first-pass dimension override)" if dimensions else ""),
        echo=True,
    )



def family_of(model: str) -> str:
    if model in H3_REF2VA_MODELS:
        return "h3"
    name = model.lower()
    for family in ("ltx", "wan", "hunyuan"):
        if name.startswith(family):
            return family
    return "unknown"


def frame_rule(model: str, frames: int) -> str | None:
    """A warning when the count breaks the family's frame rule (the decode rounds it down), else None."""
    family = family_of(model)
    step = {"ltx": 8, "wan": 4, "hunyuan": 4}.get(family)
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
            oriented = ImageOps.exif_transpose(image)
            size = oriented.size
            frames.append(np.asarray(ImageOps.fit(oriented.convert("RGB"), (width, height), Image.Resampling.LANCZOS)))
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
    """Frames leave memory at once: a writer thread feeds FFmpeg and optionally saves PNG/JPEG frames.

    ffmpeg's stderr is drained in its own thread (an unread pipe freezes it at about 64 KB), a stalled writer is
    detected by a timed queue put, and a writer that does not finish after the end is killed with the process.
    """

    def __init__(self, out: Path, ffmpeg: str | None, fps: int, log: Log, frame_format: str | None):
        self.out, self.ffmpeg, self.fps, self.log, self.frame_format = out, ffmpeg, fps, log, frame_format
        self.frames_dir = out / "frames"
        self.queue: queue.Queue = queue.Queue(maxsize=8)
        self.result = EncodeResult()
        self._proc: subprocess.Popen | None = None
        self._writer: threading.Thread | None = None
        self._drain: threading.Thread | None = None
        self._stderr: deque[str] = deque(maxlen=60)
        self._failed: str | None = None

    def _start(self, width: int, height: int) -> None:
        if self.frame_format is not None:
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
                    if self.frame_format is not None:
                        image = Image.frombytes("RGB", (width, height), rgb)
                        target = self.frames_dir / f"frame-{index:05d}.{self.frame_format}"
                        if self.frame_format == "jpg":
                            image.save(target, format="JPEG", quality=80)
                        else:
                            image.save(target, format="PNG")
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
    configuration: bytes, frames: int, shift: float | None,
    *, h3: bool = False, config_fields: list[tuple[str, object]] | None = None,
) -> bytes:
    """Apply CLI fields, enums and arrays to the exact FlatBuffer sent to DT+."""
    import flatbuffers
    from drawthings_py.generated.dt_grpc.config_generated import GenerationConfiguration, GenerationConfigurationT

    table = GenerationConfigurationT.InitFromObj(GenerationConfiguration.GetRootAs(configuration, 0))
    table.numFrames = frames
    if shift is not None:
        table.shift = shift
    if h3:
        table.stochasticSamplingGamma = 1.0
        table.resolutionDependentShift = False
    from drawthings_py.configs.gen_config import CONFIG_PROPS
    from drawthings_py.generated.dt_grpc.config_generated import CompressionMethod, SeedMode

    def normalized(name: str) -> str:
        return re.sub(r"[-_ ]", "", name).lower()

    reserved = {
        "model": "-m", "width": "-sz", "height": "-sz", "startwidth": "-sz", "startheight": "-sz",
        "numframes": "-f", "steps": "-s", "guidance": "-g", "guidancescale": "-g",
        "shift": "-sh", "seed": "-sd", "sampler": "--sampler", "negativeprompt": "-neg",
    }
    enum_types = {"seed_mode": SeedMode, "compression_artifacts": CompressionMethod}
    applied = {}
    for raw_key, supplied in config_fields or ():
        name = normalized(raw_key)
        if name in reserved:
            raise ValueError(f"Configuration {raw_key}: use {reserved[name]} instead.")
        field_name, prop = next(
            (
                (field_name, item)
                for field_name, item in CONFIG_PROPS.items()
                if name in {normalized(field_name), normalized(item.json_name), normalized(item.config_t_name)}
            ),
            (None, None),
        )
        key = prop.config_t_name if prop is not None else re.sub(r"_([a-z])", lambda m: m[1].upper(), raw_key)
        if not hasattr(table, key):
            raise ValueError(f"Unsupported Draw Things setting {raw_key!r} in the pinned SDK; it was not sent.")
        current = getattr(table, key)
        value = supplied
        if field_name in enum_types:
            enum_class = enum_types[field_name]
            choices = {
                normalized(field): number
                for field, number in vars(enum_class).items()
                if not field.startswith("_") and type(number) is int
            }
            if type(value) is int and value in choices.values():
                pass
            elif isinstance(value, str) and normalized(value) in choices:
                value = choices[normalized(value)]
            else:
                raise ValueError(
                    f"{raw_key}: expected one of {', '.join(choices)} (or its numeric value), got {value!r}."
                )
        elif field_name in ("loras", "controls"):
            if not isinstance(value, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("file"), str) or not item["file"]
                for item in value
            ):
                raise ValueError(f"{raw_key}: expected a JSON array of objects with nonempty file names.")
            converted = prop._from_json_value(value)
            if len(converted) != len(value):
                raise ValueError(f"{raw_key}: invalid list entry was rejected; nothing was sent.")
            value = prop._to_fbs_value(converted)
            if len(value) != len(converted):
                raise ValueError(f"{raw_key}: an SDK conversion dropped a list entry.")
        elif type(current) is bool:
            if type(value) is not bool:
                raise ValueError(f"{raw_key}: expected true or false, got {value!r}.")
        elif type(current) is int:
            if type(value) is not int:
                raise ValueError(f"{raw_key}: expected an integer, got {value!r}.")
            if prop is not None and getattr(prop, "unit", 1) != 1:
                # SDK JSON names use pixel dimensions; FlatBuffer stores these as 64-pixel units.
                native_hi_res_name = (
                    field_name in ("hires_fix_width", "hires_fix_height")
                    and name == normalized(prop.config_t_name)
                )
                if not native_hi_res_name:
                    if value % prop.unit:
                        raise ValueError(f"{raw_key}: expected a multiple of {prop.unit} pixels, got {value}.")
                    value = prop._to_fbs_value(value)
        elif type(current) is float:
            if type(value) not in (int, float) or not math.isfinite(float(value)):
                raise ValueError(f"{raw_key}: expected a finite number, got {value!r}.")
            value = float(value)
        elif isinstance(current, str) or current is None:
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{raw_key}: expected a string or null, got {value!r}.")
        else:
            raise ValueError(f"{raw_key}: unsupported nested field in pinned SDK.")
        setattr(table, key, value)
        applied[key] = value
    builder = flatbuffers.Builder(0)
    builder.Finish(table.Pack(builder))
    payload = bytes(builder.Output())
    roundtrip = GenerationConfigurationT.InitFromObj(GenerationConfiguration.GetRootAs(payload, 0))

    def same(a, b) -> bool:
        if isinstance(a, bytes) and isinstance(b, str):
            return a.decode("utf-8") == b
        if isinstance(b, bytes) and isinstance(a, str):
            return a == b.decode("utf-8")
        if type(a) in (int, float) and type(b) in (int, float) and (type(a) is float or type(b) is float):
            return math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-5)
        if isinstance(a, list) and isinstance(b, list):
            return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
        if hasattr(a, "__dict__") and hasattr(b, "__dict__"):
            return a.__dict__.keys() == b.__dict__.keys() and all(
                same(a.__dict__[name], b.__dict__[name]) for name in vars(a)
            )
        return a == b

    for key, value in applied.items():
        if not same(getattr(roundtrip, key), value):
            raise ValueError(f"{key}: requested value was not preserved by the FlatBuffer serializer.")
    return payload


def custom_model_spec(path: Path, model: str) -> dict:
    """Select one exact checkpoint description from a Draw Things custom.json export."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, list):
        raise ValueError(f"Expected a list of model descriptions in {path}.")
    matches = [item for item in document if isinstance(item, dict) and item.get("file") == model]
    if len(matches) != 1:
        raise ValueError(f"Expected one model description for {model} in {path}; found {len(matches)}.")
    return matches[0]


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
            if name in ("second_pass_image_encoded", "second_pass_sampling", "second_pass_image_decoded"):
                progress.second_pass_seen = True
            if name == "sampling":
                got.sampling_steps = max(got.sampling_steps, int(value.step))
                stage = (
                    f"pass 1/2: sampling step {int(value.step)} of {progress.steps}"
                    if progress.hires_requested
                    else f"sampling step {int(value.step)} of {progress.steps}"
                )
                progress.set(stage)
            elif name == "second_pass_sampling":
                progress.set(f"pass 2/2: refinement sampling step {int(value.step)}")
            elif name == "text_encoded":
                progress.set("text encoded")
            elif name == "image_encoded":
                progress.set("pass 1/2: image encoded" if progress.hires_requested else "image encoded")
            elif name == "image_decoded":
                progress.set("pass 1/2: image decoded" if progress.hires_requested else "image decoded")
            elif name == "second_pass_image_encoded":
                progress.set("pass 2/2: image encoded")
            elif name == "second_pass_image_decoded":
                progress.set("pass 2/2: image decoded")
            elif name == "face_restored":
                progress.set("face restored")
            elif name == "image_upscaled":
                progress.set("image upscaled")
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
    from dtflow.core.models import BackendError, ResolvedGenerationRequest, Sampler, TimeoutPolicy, ValidationError
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

    fps = FPS_BY_FAMILY.get(family_of(args.model), DEFAULT_FPS)
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
    h3 = args.model in H3_REF2VA_MODELS
    pika = args.model == PIKA_MODEL
    if profile is None and not h3:
        raise ValidationError(f"No bundled profile for {args.model}; cannot pick a sampler.")
    if h3:
        sampler = Sampler.TCD_TRAILING
        defaults = {"steps": 6, "guidance": 1.0, "shift": 4.0}
        label = "Pika H3 Flex" if pika else "MiniMax H3"
        log.line(f"{label} Ref2VA smoke defaults: TCD Trailing, 6 steps, guidance 1, shift 4, "
                 "stochastic sampling 100%; audio shift 1 unavailable in pinned SDK", echo=True)
    else:
        sampler = profile.sampler
        why = (
            "its own entry"
            if profile_name == args.model
            else "the family default (no entry for this file)"
            if profile_name == "family default"
            else f"the entry of {profile_name} (build tag removed)"
        )
        log.line(
            f"profile picked: {profile.name} via {why}; sampler {profile.sampler.value}, "
            f"profile steps {profile.steps}, guidance {profile.guidance}, extras {dict(profile.extras)}; base family {base_version}",
            echo=True,
        )
        extras = dict(profile.extras)
        defaults = {"steps": profile.steps, "guidance": profile.guidance, "shift": extras.get("shift")}
    if args.sampler is not None:
        sampler = Sampler(args.sampler)
    log.line(f"effective sampler: {sampler.value} ({'CLI' if args.sampler else 'model default'})", echo=True)
    sources = []
    for name, wanted, own in (
        ("steps", args.steps, defaults["steps"]),
        ("guidance", args.guidance, defaults["guidance"]),
        ("shift", args.shift, defaults["shift"]),
    ):
        value = own if wanted is None else wanted
        setattr(args, name, None if value is None else (float(value) if name != "steps" else int(value)))
        origin = "H3 smoke default" if h3 and wanted is None else "profile" if wanted is None else "argument"
        sources.append(f"{name} {value} ({origin})")
    log.line("values used: " + ", ".join(sources), echo=True)
    seed = args.seed if args.seed is not None else random.randrange(0, 2**31)
    count = len(args.images)
    log.line(
        f"request: model {args.model}, frames {args.frames}, {args.width}x{args.height}, steps {args.steps}, "
        f"guidance {args.guidance}, shift {args.shift}, seed {seed}, start images {count} "
        f"({'none' if not count else 'one start frame' if count == 1 else f'multi-frame start of {count} frames'})"
    )
    log.line(f"playback fps assumed from the Draw Things model table: {fps} (the response carries none)")
    log.line(
        "advanced settings use SDK defaults unless overridden; sent FlatBuffer values are recorded below "
        "(model/DT+ support is not guaranteed)"
    )
    if (warning := frame_rule(args.model, args.frames)) is not None:
        log.warn(warning)
    if pika and min(args.width, args.height) < 448:
        log.warn(
            f"Pika short side {min(args.width, args.height)}px is below the 448px used in a reported "
            "working Draw Things gRPC run and below its native 480p tier. "
            "This remains an exploratory, potentially nonrepresentative paid run."
        )
    if h3 and not count:
        raise ValidationError("H3 Ref2VA requires at least one -i reference image.")
    if count > args.frames:
        raise ValidationError(f"{count} start images but only {args.frames} frames requested.")
    start_frames = await asyncio.to_thread(load_images, args.images, args.width, args.height, log) if count else None
    if count:
        log.line("  images are cover-fitted (resized, centre-cropped) to the request size")

    request = ResolvedGenerationRequest(
        request_id=uuid.uuid4().hex,
        prompt=args.prompt,
        negative_prompt=args.negative,
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

    frame_format = "jpg" if args.jpg_frames else "png" if args.keep_frames or args.no_mp4 else None
    sink = FrameSink(args.out, ffmpeg if encode else None, fps, log, frame_format)
    log.line(f"frame image output: {frame_format or 'none (MP4 stream only)'}", echo=True)
    got = Received()
    progress = Progress(
        not args.quiet, args.steps, args.frames, hires_requested=args.hires_factor > 1
    )
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
            if args.model_specs is not None:
                spec = custom_model_spec(args.model_specs, args.model)
                log.line(f"using exact model description from {args.model_specs}", echo=True)
            if h3 and spec is None:
                raise ValidationError(
                    f"No model description available for {args.model}; "
                    "supply -spec with an exact Draw Things model-description export."
                )
            log.line(
                f"model description for DT+: advertised by Echo {args.model in backend._model_specs}, sent {bool(spec)}"
            )
            wire = build_generation_message(request, None, base_version, spec)
            message = wire.message
            message.configuration = patch_configuration(
                message.configuration, args.frames, args.shift, h3=h3, config_fields=args.config_fields
            )
            config = sent_configuration(message.configuration)
            log.line("GenConfig fields sent: " + json.dumps(config, sort_keys=True, default=str))
            if args.config_fields:
                log.line(f"applied Draw Things configuration overrides: {args.config_fields}", echo=True)
            from dtflow.backends.drawthings.wire import SAMPLER_NAME, SAMPLER_WIRE_VALUE
            from drawthings_py.generated.dt_grpc.config_generated import GenerationConfiguration, GenerationConfigurationT

            raw = GenerationConfiguration.GetRootAs(message.configuration, 0)
            if raw.Sampler() != SAMPLER_WIRE_VALUE[SAMPLER_NAME[sampler]]:
                raise ValidationError(f"Sampler {sampler.value} was not serialized as requested.")
            if h3:
                resolved = GenerationConfigurationT.InitFromObj(raw)
                if not math.isfinite(resolved.stochasticSamplingGamma):
                    raise ValidationError("H3 stochastic sampling gamma was not serialized.")
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
    if progress.hires_requested:
        if progress.second_pass_seen:
            log.line("Hires Fix second-pass progress: reported by DT+ signposts", echo=True)
        else:
            log.warn(
                "Hires Fix was requested but DT+ reported no second-pass signposts; "
                "a refinement pass cannot be confirmed from this response."
            )
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
        log.line(f"-nomp4: no encode; {sink.frame_format.upper()} frames saved in {sink.frames_dir} ({result.frames} written)")
        found.ok = result.error is None and frames_in == args.frames
        found.outputs.append(str(sink.frames_dir))
        found.verdict = f"{frames_in}/{args.frames} frames received at {returned[1]}x{returned[0]}, {sink.frame_format.upper()} only (-nomp4)"
        if frames_in != args.frames:
            found.verdict += "; incomplete video, NOT a successful generation"
        return
    log.line(f"ffmpeg command: {result.command}")
    log.line(f"ffmpeg exit code: {result.returncode}; frames written {result.frames}")
    if result.stderr_tail:
        log.line("ffmpeg stderr tail:\n" + result.stderr_tail)
    if result.error or result.returncode != 0 or result.video_partial is None:
        found.verdict = f"{frames_in} frames received but the encode failed: {result.error or 'ffmpeg exit ' + str(result.returncode)}"
        if sink.frame_format is not None:
            found.outputs.append(str(sink.frames_dir))
            log.line("encode failed; saved frames are kept for inspection (nothing half-written is published)")
        else:
            log.line("encode failed; frame images were not requested (nothing half-written is published)")
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
        if sink.frame_format is None:
            audio_raw_path.unlink(missing_ok=True)
    else:
        os.replace(result.video_partial, final)
        log.line(f".partial publish: {final}")
    found.outputs.append(str(final))
    if sink.frame_format is not None:
        found.outputs.append(str(sink.frames_dir))
        log.line(f"{sink.frame_format.upper()} frames kept in {sink.frames_dir} ({result.frames} written)")
    else:
        log.line("frames streamed directly to FFmpeg; no still images written (use -kf or -jpg to save them)")

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
            log.line(f"  {name} = {value}")
        log.line("defaults resolved:")
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
