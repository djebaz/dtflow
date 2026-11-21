"""Save one video attempt: the MP4 beside its poster PNG, plus the usual metadata sidecar.

Storage option B (Djé, 2026-11-21): the poster is the gallery's ordinal 0 image (``<prefix>-<attempt>-1.png``) and the
clip is its companion ``<prefix>-<attempt>-1.mp4``. The sidecar's ``video`` block describes the clip as probed.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from dtflow.core.models import GenerationResult, ResolvedGenerationRequest, ResourceLimits
from dtflow.core.output_layout import OutputLayout, final_name, sidecar_name
from dtflow.services.artifacts import artifact_record, atomic_write, generation_metadata, run_directory


def video_path_for(poster: str | Path) -> Path:
    """The companion clip of a managed poster path."""
    return Path(poster).with_suffix(".mp4")


def save_video_artifacts(
    output: Path,
    attempt_id: str,
    request: ResolvedGenerationRequest,
    result: GenerationResult,
    build_identity: dict[str, str],
    limits: ResourceLimits,
    layout: OutputLayout | None = None,
) -> tuple[tuple[str, ...], str]:
    artifact = result.artifacts[0]
    facts = dict(artifact.metadata)
    temporary = Path(facts.pop("video_temp_path"))
    directory, prefix = run_directory(output, attempt_id, request, layout)
    poster = directory / final_name(prefix, attempt_id, 0)
    clip = video_path_for(poster)
    sidecar = directory / sidecar_name(prefix, attempt_id)
    if sidecar.exists() or poster.exists() or clip.exists():
        raise FileExistsError("Managed output already exists for this attempt.")
    partial = clip.with_name(clip.name + ".partial")
    try:
        shutil.move(str(temporary), partial)  # another drive is possible, so not os.replace on the source
        os.replace(partial, clip)
    finally:
        partial.unlink(missing_ok=True)
    shutil.rmtree(temporary.parent, ignore_errors=True)
    atomic_write(poster, artifact.encoded)
    record = artifact_record(str(poster.resolve()), artifact)
    record["metadata"] = {key: value for key, value in record["metadata"].items() if not key.startswith("video_")}
    metadata = generation_metadata(attempt_id, request, result, build_identity, [record])
    metadata["video"] = {
        "file": clip.name,
        "frames": int(facts["video_frames"]),
        "fps": float(facts["video_fps"]),
        "duration_seconds": float(facts["video_duration_seconds"]),
        "has_audio": facts["video_has_audio"] == "yes",
        "audio_rate_hz": int(facts["video_audio_rate"]) or None,
        "bytes": int(facts["video_bytes"]),
        "width_px": request.width_px,
        "height_px": request.height_px,
        "poster": poster.name,
    }
    atomic_write(sidecar, (json.dumps(metadata, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    return (str(poster.resolve()),), str(sidecar.resolve())
