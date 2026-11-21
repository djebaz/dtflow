"""Video generation rules as data: families, frame counts, sizes, playback fps, audio rate and profile defaults.

Pure functions, no I/O besides the bundled profile dataset. Values come from the video POC runs on DT+
(``devdocs/research/2026-11-21-video-generation-poc.md``); a new family adds a row to ``FAMILIES``, not a code branch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dtflow.core.model_profiles import ModelProfile, profile_for
from dtflow.core.models import ValidationError


@dataclass(frozen=True, slots=True)
class VideoFamily:
    """One video family: the frame-count rule ``step * k + 1`` and the DTFlow-side playback data (not on the wire)."""

    prefix: str
    label: str
    frame_step: int
    default_frames: int
    fps: int
    audio_rate: int | None  # Hz of the audio tensor DT+ sends, None when the family has no audio
    default_size: tuple[int, int]


# Longest prefix first: the first matching row wins.
FAMILIES: tuple[VideoFamily, ...] = (
    VideoFamily("ltx_2.3", "LTX-2.3", 8, 121, 25, 48000, (320, 320)),
    VideoFamily("ltx_2", "LTX-2", 8, 121, 25, 24000, (320, 320)),
    VideoFamily("ltx", "LTX", 8, 121, 25, 24000, (320, 320)),
    VideoFamily("wan_v2.2", "Wan 2.2", 4, 81, 24, None, (480, 320)),
    VideoFamily("wan_v2.1", "Wan 2.1", 4, 81, 16, None, (480, 320)),
    VideoFamily("wan", "Wan", 4, 81, 16, None, (480, 320)),
)

MAX_FRAMES = 361
MIN_FRAMES = 9
# Only these exact files are authorized for the initial production LTX slice.
# #523 established the 1.1 q6p path; the owner supplied two successful q8p Windows runs in PR #527.
# A new quantization or similarly named checkpoint needs its own evidence before admission.
SUPPORTED_VIDEO_MODELS = frozenset(
    {
        "ltx_2.3_22b_distilled_1.1_q6p.ckpt",
        "ltx_2.3_22b_distilled_q8p.ckpt",
    }
)
START_IMAGE_EVIDENCE = {
    "ltx_2.3_22b_distilled_1.1_q6p.ckpt": "video-poc-523",
    "ltx_2.3_22b_distilled_q8p.ckpt": "video-windows-527",
}
# Real runs took up to 243 s at small sizes; the image default of 600 s is not enough for a bigger clip.
VIDEO_TOTAL_TIMEOUT = 1800.0


def family_of(model_id: str) -> VideoFamily | None:
    name = model_id.lower()
    return next((row for row in FAMILIES if name.startswith(row.prefix)), None)


def is_video_model(model_id: str) -> bool:
    """Recognize a video checkpoint even when this production slice cannot dispatch it."""
    return family_of(model_id) is not None


def supported_video_model(model_id: str) -> bool:
    return model_id in SUPPORTED_VIDEO_MODELS


def accepts_start_image(model_id: str) -> bool:
    return model_id in START_IMAGE_EVIDENCE


def snap_side(value: int) -> int:
    """Nearest multiple of 64 (ties go up), kept inside 128..2048."""
    return min(2048, max(128, (value + 32) // 64 * 64))


def valid_frames(model_id: str, frames: int) -> int:
    """The nearest count the family's rule allows (rounded down, never below the minimum); unknown families keep it."""
    family = family_of(model_id)
    if family is None:
        return frames
    frames = max(MIN_FRAMES, min(MAX_FRAMES, frames))
    return (frames - 1) // family.frame_step * family.frame_step + 1


def frame_problem(model_id: str, frames: int) -> str | None:
    family = family_of(model_id)
    if family is None:
        return None
    if not MIN_FRAMES <= frames <= MAX_FRAMES:
        return f"frames must be in {MIN_FRAMES}..{MAX_FRAMES}."
    if (frames - 1) % family.frame_step:
        return f"{family.label} needs {family.frame_step}k+1 frames (for example {valid_frames(model_id, frames)})."
    return None


def playback_fps(model_id: str) -> int:
    family = family_of(model_id)
    return family.fps if family else 30


def audio_rate(model_id: str) -> int | None:
    family = family_of(model_id)
    return family.audio_rate if family else None


def duration_seconds(model_id: str, frames: int) -> float:
    return frames / playback_fps(model_id)


def duration_text(seconds: float) -> str:
    return f"{seconds:.1f} s" if seconds < 60 else f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def profile_candidates(model_id: str) -> list[str]:
    """The file itself, then without a ``_1.1`` style build tag (the profile dataset has no such entries)."""
    stripped = re.sub(r"_\d+\.\d+(?=_(?:q\d|f16|f32|bf16))", "", model_id)
    return [model_id] if stripped == model_id else [model_id, stripped]


def video_profile(model_id: str) -> ModelProfile | None:
    """The model's own profile (exact entry first); never a profile of another family."""
    for candidate in profile_candidates(model_id):
        profile = profile_for(candidate)
        if profile is not None:
            return profile
    return None


def video_shift(model_id: str) -> float | None:
    profile = video_profile(model_id)
    value = dict(profile.extras).get("shift") if profile else None
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def check_request_fields(model_id: str, frames: int, start_image: str | None) -> None:
    """Request validation shared by the request model: frame rule and start-image support."""
    if not supported_video_model(model_id):
        raise ValidationError("This exact checkpoint is not yet verified for video generation.")
    problem = frame_problem(model_id, frames)
    if problem:
        raise ValidationError(f"num_frames: {problem}")
    if start_image is not None and not accepts_start_image(model_id):
        raise ValidationError("This video model is not verified for a start image; use text-to-video.")


def clip_summary(request: dict) -> tuple[str, str] | None:
    """``(duration, details)`` for a recorded request dict that is a video, else None (images stay unchanged)."""
    frames, model = request.get("num_frames"), request.get("model_id")
    if type(frames) is not int or not isinstance(model, str):
        return None
    seconds = duration_seconds(model, frames)
    sound = "with sound" if audio_rate(model) else "no sound"
    return duration_text(seconds), f"{duration_text(seconds)} · {playback_fps(model)} fps · {frames} frames · {sound}"
