"""Flat JPEG export of committed saved outputs; no Qt, providers or execution writes."""

import asyncio
import hashlib
import io
import json
import os
import re
import threading
import unicodedata
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

from PIL import Image

from dtflow.core.export import ExportOptions, ExportResult
from dtflow.core.gallery import GalleryItem, GalleryQuery
from dtflow.core.models import ValidationError
from dtflow.core.tasks import settle
from dtflow.persistence import gallery as storage
from dtflow.persistence.store import PersistenceError
from dtflow.services.gallery import item_from_record, load_detail, safe_metadata

MAX_EXPORT_IMAGES = 10_000
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 32_000_000
MAX_EXIF_BYTES = 65_533
# Export a reviewed portable subset, not arbitrary provider strings, account identifiers or file paths.
REQUEST_FIELDS = (
    "model_id",
    "seed",
    "width_px",
    "height_px",
    "steps",
    "guidance",
    "sampler_id",
    "operation",
    "batch_size",
    "batch_count",
    "allow_unlisted_model",
)
EFFECTIVE_FIELDS = frozenset(
    """
    aesthetic_score batch_count batch_size causal_inference causal_inference_enabled causal_inference_pad
    cfg_zero_init_steps cfg_zero_star clip_skip clip_weight compression_artifacts compression_artifacts_quality
    config_source crop_left crop_top decoding_tile_height decoding_tile_overlap decoding_tile_width
    diffusion_tile_height diffusion_tile_overlap diffusion_tile_width fps guidance guidance_embed
    guiding_frame_guidance guiding_frame_noise height hires_fix hires_fix_height hires_fix_strength hires_fix_width
    image_guidance_scale image_prior_steps mask_blur mask_blur_outset model motion_scale negative_aesthetic_score
    negative_original_image_height negative_original_image_width negative_prompt_for_image_prior num_frames
    original_image_height original_image_width preserve_original_after_inpaint refiner_start resolution_dependent_shift
    sampler sampler_wire_value seed seed_mode separate_clip_l separate_open_clip_g separate_t5 sharpness shift
    speed_up_with_guidance_embed stage_2_cfg stage_2_shift stage_2_steps steps stochastic_sampling_gamma strength
    t5_text_encoder target_image_height target_image_width tea_cache tea_cache_end tea_cache_max_skip_steps
    tea_cache_start tea_cache_threshold tiled_decoding tiled_diffusion upscaler_scale_factor width zero_negative_prompt
""".split()
)


def _identifier(value) -> str:
    """Keep portable model identity; never export an absolute local model/LoRA path."""
    text = str(value or "")
    if PureWindowsPath(text).drive or text.startswith(("/", "\\")):
        return PureWindowsPath(text).name
    return text


def _portable(item: GalleryItem, options: ExportOptions) -> tuple[dict, list[str]]:
    detail = load_detail(item)
    recorded = json.loads(detail.metadata_json)
    request = json.loads(item.request_json)
    requested = {key: request[key] for key in REQUEST_FIELDS if key in request}
    if "model_id" in requested:
        requested["model_id"] = _identifier(requested["model_id"])
    requested["loras"] = [
        {"identifier": _identifier(x.get("identifier")), "weight": x.get("weight")}
        for x in request.get("loras", [])
        if isinstance(x, dict)
    ]
    if options.include_prompts:
        requested.update(prompt=request.get("prompt", ""), negative_prompt=request.get("negative_prompt", ""))
    raw_effective = recorded.get("effective_configuration", {})
    effective = (
        {
            key: (_identifier(value) if key == "model" else value)
            for key, value in raw_effective.items()
            if key in EFFECTIVE_FIELDS and isinstance(value, (str, int, float, bool))
        }
        if isinstance(raw_effective, dict)
        else {}
    )
    build = recorded.get("build", {})
    selected_artifact = next(
        (x for x in recorded.get("artifacts", []) if isinstance(x, dict) and x.get("path") == item.path), {}
    )
    artifact_meta = selected_artifact.get("metadata", {})
    document = {
        "export_schema_version": 1,
        "attempt_id": item.attempt_id,
        "request_id": item.request_id,
        "source_output_identity": item.identity,
        "saved_at_utc": item.saved_at_utc,
        "timestamp_meaning": "durable saved time, not generation start time",
        "backend_id": item.backend_id,
        "requested_configuration": requested,
        "recorded_effective_configuration": effective,
        "seed_meaning": "request/effective configuration seed; per-image seed not independently verified",
        "artifact": {
            "artifact_id": selected_artifact.get("artifact_id", item.identity),
            "output_index": int(item.identity.rsplit(":", 1)[1]) + 1,
            "width_px": item.width,
            "height_px": item.height,
        },
        "generation_build": {
            key: build[key]
            for key in ("version", "source_commit", "source_status", "source_modified")
            if isinstance(build, dict) and key in build
        },
        "metadata_status": "durable request fallback" if detail.issue else "recorded sidecar",
        "prompts_included": options.include_prompts,
    }
    if isinstance(artifact_meta, dict) and "tensor_channels" in artifact_meta:
        document["artifact"]["tensor_channels"] = artifact_meta["tensor_channels"]
    timing = recorded.get("timing", {})
    if isinstance(timing, dict) and isinstance(timing.get("backend_elapsed_seconds"), (int, float)):
        document["timing"] = {"backend_elapsed_seconds": timing["backend_elapsed_seconds"]}
    # Source lineage is an output identity only, never a file path or prepared-asset receipt.
    lineage = next(
        (
            pair[1]
            for pair in request.get("resolution_evidence", [])
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] == "source.output"
        ),
        None,
    )
    if isinstance(lineage, str) and re.fullmatch(r"[a-f0-9]{32}:\d+", lineage):
        document["source_lineage"] = lineage
    notes = (
        ["Some outputs used durable request evidence because their sidecar was unavailable."] if detail.issue else []
    )
    return safe_metadata(document), notes


def _slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", text.lower())[:6]
    return "-".join(words)[:40].rstrip("-") or "image"


def _filename(item: GalleryItem, document: dict) -> str:
    try:
        saved = datetime.fromisoformat(item.saved_at_utc)
        if saved.tzinfo is None:
            raise ValueError
        stamp = saved.astimezone(UTC).strftime("%Y%m%d-%H%M%SZ")
    except ValueError:
        stamp = "undated"
    request = document["requested_configuration"]
    seed = request.get("seed")
    seed_text = str(seed) if type(seed) is int else "unknown"
    attempt = re.sub(r"[^a-zA-Z0-9]", "", item.attempt_id)[:8] or "attempt"
    return (
        f"{stamp}_{_slug(request.get('prompt', ''))}_s{seed_text}_{attempt}_{document['artifact']['output_index']:02d}"
    )


def _exif(document: dict, sidecar_name: str | None = None) -> bytes:
    request = document["requested_configuration"]
    loras = "; ".join(f"{x['identifier']} @ {x['weight']}" for x in request.get("loras", [])) or "none"
    comment = (
        f"Model: {request.get('model_id', 'unknown')}\nRequested seed: {request.get('seed', 'unknown')} | "
        f"Steps: {request.get('steps', 'unknown')} | Guidance: {request.get('guidance', 'unknown')}\n"
        f"Size: {document['artifact']['width_px']}x{document['artifact']['height_px']} | "
        f"Sampler: {request.get('sampler_id', 'unknown')}\nLoRAs: {loras}\n"
        f"Provider: {document['backend_id']} | Saved (UTC): {document['saved_at_utc']}\n"
        f"Attempt: {document['attempt_id']} | Output: {document['artifact']['output_index']}"
    )
    if document["prompts_included"]:
        comment += f"\nPrompt: {request.get('prompt', '')}\nNegative prompt: {request.get('negative_prompt', '')}"
    exif = Image.Exif()
    for tag, value in (
        (40091, request.get("prompt", "")[:160] or "DTFlow image"),
        (40095, f"DTFlow | {request.get('operation', 'unknown')} | {request.get('model_id', 'unknown')}"[:300]),
        (40092, comment[:1800]),
        (
            40094,
            f"DTFlow;{request.get('model_id', 'unknown')};Seed-{request.get('seed', 'unknown')};"
            f"Steps-{request.get('steps', 'unknown')};{request.get('sampler_id', 'unknown')};{loras}"[:1000],
        ),
    ):
        exif[tag] = value.encode("utf-16-le") + b"\0\0"
    if document["prompts_included"]:
        # Match the PoC's bounded ASCII-compatible description. Full Unicode prompts
        # remain in XP fields and portable JSON; privacy-off omits this tag entirely.
        exif[270] = request.get("prompt", "")[:160].encode("ascii", "replace").decode("ascii")
    # Camera, author and copyright tags are deliberately not repurposed.
    exif[305] = "DTFlow " + str(document.get("generation_build", {}).get("version", "unknown"))
    full = (
        document
        if sidecar_name is None
        else {
            "export_schema_version": 1,
            "metadata_sidecar": sidecar_name,
            "note": "Full portable metadata exceeds JPEG EXIF capacity; see accompanying JSON.",
        }
    )
    exif[34665] = {
        37510: b"UNICODE\0" + json.dumps(full, ensure_ascii=False, allow_nan=False).encode("utf-16"),
        40962: document["artifact"]["width_px"],
        40963: document["artifact"]["height_px"],
    }
    return exif.tobytes()


def _reserve(destination: Path, stem: str):
    """Exclusively reserve both names. No overwrite, including concurrent exporters."""
    for suffix in range(10_000):
        name = stem if suffix == 0 else f"{stem}_{suffix:02d}"
        image, sidecar = destination / f"{name}.jpg", destination / f"{name}.json"
        if sidecar.exists():
            continue
        try:
            image_handle = image.open("xb")
        except FileExistsError:
            continue
        try:
            sidecar_handle = sidecar.open("xb")
        except FileExistsError:
            image_handle.close()
            image.unlink()
            continue
        except BaseException:
            image_handle.close()
            image.unlink()
            raise
        return image, sidecar, image_handle, sidecar_handle
    raise ValidationError("Too many filename collisions in the export folder.")


def _export_one(item: GalleryItem, destination: Path, options: ExportOptions) -> tuple[str, ...]:
    source = Path(item.path).resolve()
    if destination == source.parent or destination.is_relative_to(source.parent):
        raise ValidationError("Choose an export folder outside the original attempt folder.")
    if not source.is_file() or source.stat().st_size > MAX_IMAGE_BYTES:
        raise ValidationError("Image is missing, unreadable or exceeds the 64 MiB export limit.")
    with source.open("rb") as handle:
        original = handle.read(MAX_IMAGE_BYTES + 1)
    if len(original) > MAX_IMAGE_BYTES:
        raise ValidationError("Image exceeds the 64 MiB export limit.")
    document, notes = _portable(item, options)
    document.update(
        source_png_sha256=hashlib.sha256(original).hexdigest(),
        exported_at_utc=datetime.now(UTC).isoformat(),
        encoding={"format": "JPEG", "quality": 95, "subsampling": "4:4:4", "lossy": True, "alpha_background": "white"},
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(original)) as decoded:
            if decoded.format != "PNG" or decoded.width * decoded.height > MAX_PIXELS:
                raise ValidationError("Export requires a PNG of at most 32 million pixels.")
            if decoded.size != (item.width, item.height):
                raise ValidationError("Image dimensions disagree with the durable output record.")
            decoded.load()
            if "A" in decoded.getbands() or "transparency" in decoded.info:
                rgba = decoded.convert("RGBA")
                rgb = Image.new("RGB", decoded.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
                rgba.close()
            else:
                rgb = decoded.convert("RGB")
            try:
                stem = _filename(item, document)
                image, sidecar, image_handle, sidecar_handle = _reserve(destination, stem)
                keep_sidecar = options.json_sidecars
                try:
                    embedded = _exif(document)
                    if len(embedded) > MAX_EXIF_BYTES:
                        embedded = _exif(document, sidecar.name)
                        keep_sidecar = True
                        notes.append("Oversized EXIF metadata was preserved in a required JSON sidecar.")
                    if len(embedded) > MAX_EXIF_BYTES:
                        raise ValidationError("Metadata summary exceeds the JPEG EXIF limit.")
                    # No provider EXIF/XMP is copied blindly; preserve an existing color profile only.
                    rgb.save(
                        image_handle,
                        format="JPEG",
                        quality=95,
                        subsampling=0,
                        exif=embedded,
                        icc_profile=decoded.info.get("icc_profile"),
                    )
                    image_handle.flush()
                    os.fsync(image_handle.fileno())
                    if keep_sidecar:
                        sidecar_handle.write(
                            (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
                        )
                        sidecar_handle.flush()
                        os.fsync(sidecar_handle.fileno())
                except BaseException:
                    image_handle.close()
                    sidecar_handle.close()
                    image.unlink(missing_ok=True)
                    sidecar.unlink(missing_ok=True)
                    raise
                else:
                    image_handle.close()
                    sidecar_handle.close()
                    if not keep_sidecar:
                        sidecar.unlink()
            finally:
                rgb.close()
    return tuple(notes)


def _run_export(state, destination, options, query, identities, cancel, progress):
    destination = Path(destination).expanduser().resolve()
    if destination == Path(state).resolve() or destination.is_relative_to(Path(state).resolve()):
        raise ValidationError("Choose an export folder outside the application state directory.")
    if identities is None:
        identities = storage.export_identities(state, query or GalleryQuery(), MAX_EXPORT_IMAGES)
    else:
        identities = tuple(dict.fromkeys(identities))
    if len(identities) > MAX_EXPORT_IMAGES:
        raise ValidationError("Export is limited to 10,000 images per operation; narrow the Images filter.")
    total = len(identities)
    if not total or cancel.is_set():
        return ExportResult(str(destination), total, 0, 0, cancel.is_set())
    destination.mkdir(parents=True, exist_ok=True)
    exported, failed = 0, 0
    notes, failures = set(), []
    if progress:
        progress(0, total)
    for index, identity in enumerate(identities):
        if cancel.is_set():
            break
        try:
            record = storage.get_record(state, identity)
            if record is None:
                raise ValidationError("Saved output is no longer available.")
            notes.update(_export_one(item_from_record(record), destination, options))
            exported += 1
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RecursionError,
            PersistenceError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            failed += 1
            if len(failures) < 100:
                # Never expose decoder/OS exception strings, which can contain paths or source data.
                reason = (
                    str(exc)
                    if isinstance(exc, (ValidationError, PersistenceError))
                    else f"could not export ({type(exc).__name__})"
                )
                failures.append(f"{identity}: {reason}")
        if progress:
            progress(index + 1, total)
    return ExportResult(
        str(destination), total, exported, failed, cancel.is_set(), tuple(sorted(notes)), tuple(failures)
    )


async def export_outputs(
    state: Path,
    destination: Path,
    options: ExportOptions | None = None,
    *,
    query: GalleryQuery | None = None,
    identities: tuple[str, ...] | None = None,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ExportResult:
    """Snapshot a filter or explicit committed identities, then export sequentially off-thread.

    All JPGs/optional JSONs are directly in destination, never grouped in subfolders.
    Cancel cooperatively between images; settle an in-flight file write before shutdown.
    """
    stop = cancel if cancel is not None else threading.Event()
    worker = asyncio.create_task(
        asyncio.to_thread(
            _run_export,
            Path(state),
            Path(destination),
            options or ExportOptions(),
            query,
            identities,
            stop,
            progress,
        )
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        stop.set()
        await settle(worker)
        raise
