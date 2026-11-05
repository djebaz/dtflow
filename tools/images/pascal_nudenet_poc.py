#!/usr/bin/env python3
"""CPU-only Pascal-7 anatomy with competing NudeNet-seeded, edge-guided zones.

Missing pinned checkpoints download automatically; --no-download forbids downloads.
Processes only the largest person. Outputs are flat and source-prefixed. NudeNet
zones are heuristic refinements, not trained pixel-level anatomical predictions.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from schp_atr18_smoke import (
    CHECKPOINTS,
    IMAGE_SUFFIXES,
    MEAN,
    SIDE,
    STD,
    detect_body_parts,
    ensure_checkpoint,
    load_rgb,
    load_session,
    person_crop_box,
    select_people,
    sha256_file,
)

REVISION = "50b9f67426dad72715f2ead54fce0fdf0117a0cd"
PASCAL_SPEC = (
    f"https://huggingface.co/pirocheto/schp-pascal-7/resolve/{REVISION}/onnx/schp-pascal-7-int8-static.onnx",
    69_148_800,
    "sha256",
    "66b12766d7f1ddbc3de972e67e8626be727507e7feeeca34e1b23b6f45e756d2",
)
LABELS = ("background", "head", "torso", "upper_arms", "lower_arms", "upper_legs", "lower_legs")
UNKNOWN = 65535


def verified_checkpoint(path, spec, allow_download):
    """Check existing files too; never silently reuse a different checkpoint."""
    ensure_checkpoint(path, spec, allow_download=allow_download)
    _, expected_size, algorithm, expected_hash = spec
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise RuntimeError(
            f"Checkpoint size mismatch: {path}; expected {expected_size:,} bytes, found {actual_size:,}. "
            "This POC requires its pinned checkpoint; use a different model path for the automatic download. "
            "The existing file was not changed."
        )
    if algorithm == "sha256":
        actual = sha256_file(path)
    else:
        import hashlib

        digest = hashlib.sha1(f"blob {expected_size}\0".encode("ascii"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
    if actual != expected_hash:
        raise RuntimeError(f"Checkpoint digest mismatch: {path}; move it explicitly before retrying")


def anatomy(session, crop):
    """Mirror the pinned processor's direct RGB resize and BGR-indexed constants."""
    rgb = np.asarray(crop.resize((SIDE, SIDE), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    rgb = (rgb - np.asarray(MEAN, dtype=np.float32)) / np.asarray(STD, dtype=np.float32)
    tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32)
    logits = session.run(["logits"], {"pixel_values": tensor})[0]
    if logits.shape != (1, 7, SIDE, SIDE) or not np.isfinite(logits).all():
        raise RuntimeError(f"Invalid Pascal-7 logits: {logits.shape}")
    # Restore each logit plane before argmax without a seven-channel full-size allocation.
    maximum = np.full((crop.height, crop.width), -np.inf, dtype=np.float32)
    denominator = np.zeros_like(maximum)
    labels = np.zeros(maximum.shape, dtype=np.uint8)
    for index, plane in enumerate(logits[0]):
        score = np.asarray(Image.fromarray(plane).resize(crop.size, Image.Resampling.BILINEAR), dtype=np.float32)
        updated = np.maximum(maximum, score)
        denominator = denominator * np.exp(maximum - updated) + np.exp(score - updated)
        labels[score > maximum] = index
        maximum = updated
    return labels, 1.0 / denominator


def compatible_parts(name):
    """Conservative coarse support; Pascal-7 has no fine torso or left/right labels."""
    if name.startswith("FACE_"):
        return (1,)
    if name.startswith("FEET_"):
        return (6,)
    if name.startswith("ARMPITS_"):
        return (2, 3)
    if name.startswith(("BUTTOCKS_", "ANUS_", "FEMALE_GENITALIA_", "MALE_GENITALIA_")):
        return (2, 5)
    if "BREAST" in name or name.startswith("BELLY_"):
        return (2,)
    return ()


def competing_zones(crop, inside, raw, confidence, detections, resolution, min_confidence):
    """Multi-source geodesic growth with soft RGB costs and hard anatomy/box limits.

    Seeds compete in one label map. Growth cannot cross Pascal class boundaries
    except where a NudeNet label explicitly permits both classes. Unreachable or
    unsupported pixels remain unknown instead of being filled arbitrarily.
    """
    scale = min(1.0, resolution / max(crop.size))
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))

    def resize(data, method):
        return np.asarray(Image.fromarray(data).resize(size, method))

    parts = resize(raw, Image.Resampling.NEAREST)
    valid = resize(inside.astype(np.uint8), Image.Resampling.NEAREST) > 0
    scores = resize(confidence.astype(np.float32), Image.Resampling.BILINEAR)
    supported = valid & (parts > 0) & (scores >= min_confidence)
    rgb = (
        np.asarray(crop.resize(size, Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(0.8)), dtype=np.float32)
        / 255.0
    )
    height, width = parts.shape
    zones = np.where(supported, parts, 0).astype(np.uint16)
    limits = {}
    reports = []
    cores = []
    yy, xx = np.ogrid[:height, :width]
    sx, sy = width / crop.width, height / crop.height
    for index, detection in enumerate(detections, 7):
        allowed = compatible_parts(detection["label"])
        x0, y0, x1, y1 = detection["box_xyxy"]
        x0, x1, y0, y1 = x0 * sx, x1 * sx, y0 * sy, y1 * sy
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = max(1.0, (x1 - x0) * 0.2), max(1.0, (y1 - y0) * 0.2)
        margin_x, margin_y = (x1 - x0) * 0.1, (y1 - y0) * 0.1
        bounds = (
            max(0, math.floor(x0 - margin_x)),
            max(0, math.floor(y0 - margin_y)),
            min(width, math.ceil(x1 + margin_x)),
            min(height, math.ceil(y1 + margin_y)),
        )
        eligible = supported & np.isin(parts, allowed)
        core = eligible & (((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1)
        report = dict(
            detection,
            zone_id=index,
            allowed_pascal_ids=list(allowed),
            status="seeded" if core.any() else "rejected_no_supported_core",
        )
        reports.append(report)
        if not core.any():
            continue
        limits[index] = (allowed, bounds)
        cores.append((index, core))
        bx0, by0, bx1, by1 = bounds
        region = zones[by0:by1, bx0:bx1]
        region[eligible[by0:by1, bx0:bx1]] = 0
    # Detections arrive score-sorted. Higher scores retain overlapping seed pixels.
    claimed = np.zeros(parts.shape, dtype=bool)
    for index, core in cores:
        available = core & ~claimed
        zones[available] = index
        claimed |= available
        if not available.any():
            reports[index - 7]["status"] = "rejected_overlapping_core"

    distance = np.full(parts.shape, np.inf, dtype=np.float32)
    distance[zones > 0] = 0
    pending = supported & (zones == 0)
    adjacent = np.zeros_like(pending)
    adjacent[1:] |= pending[:-1]
    adjacent[:-1] |= pending[1:]
    adjacent[:, 1:] |= pending[:, :-1]
    adjacent[:, :-1] |= pending[:, 1:]
    queue = [(0.0, int(zones[y, x]), int(y), int(x)) for y, x in zip(*np.nonzero(adjacent & (zones > 0)), strict=True)]
    heapq.heapify(queue)
    while queue:
        cost, zone, y, x = heapq.heappop(queue)
        if cost > float(distance[y, x]) + 1e-5 or zones[y, x] != zone:
            continue
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if not (0 <= ny < height and 0 <= nx < width) or not pending[ny, nx]:
                continue
            if zone < 7:
                if parts[ny, nx] != zone:
                    continue
            else:
                allowed, (bx0, by0, bx1, by1) = limits[zone]
                if parts[ny, nx] not in allowed or not (bx0 <= nx < bx1 and by0 <= ny < by1):
                    continue
            step = 0.02 + float(np.linalg.norm(rgb[ny, nx] - rgb[y, x]))
            step += 0.25 * abs(float(scores[ny, nx] - scores[y, x]))
            candidate = cost + step
            if candidate < distance[ny, nx]:
                distance[ny, nx] = candidate
                zones[ny, nx] = zone
                heapq.heappush(queue, (float(distance[ny, nx]), zone, ny, nx))
    result = np.asarray(Image.fromarray(zones).resize(crop.size, Image.Resampling.NEAREST)).copy()
    result[~inside] = 0
    result[inside & ((raw == 0) | (confidence < min_confidence) | (result == 0))] = UNKNOWN
    # Nearest-neighbor restoration must not cross source-resolution anatomy boundaries.
    for index in range(1, 7):
        result[(result == index) & (raw != index)] = UNKNOWN
    for index, (allowed, _) in limits.items():
        result[(result == index) & ~np.isin(raw, allowed)] = UNKNOWN
    for report in reports:
        report["pixels"] = int(np.count_nonzero(result == report["zone_id"]))
        if report["status"] == "seeded":
            report["status"] = "assigned" if report["pixels"] else "empty_after_restoration"
    return result, reports


def save_preview(
    source, box, inside, labels, names, destination, heading, notes, palette=None, antialias=False, contour_blur=0
):
    """Render an overlay with its own visible-label legend outside the photograph."""
    x0, y0, x1, y1 = box
    full = np.zeros((source.height, source.width), dtype=np.uint16)
    full[y0:y1, x0:x1] = labels
    seed = np.arange(65536, dtype=np.uint32) * np.uint32(2654435761)
    color_table = np.stack(((seed >> 16) & 255, (seed >> 8) & 255, seed & 255), axis=-1).astype(np.uint8)
    color_table[0] = 0
    color_table[UNKNOWN] = (128, 128, 128)
    for label, shade in (palette or {}).items():
        color_table[label] = shade
    color = color_table[full]
    rgb = np.asarray(source).copy()
    active = full > 0
    if antialias:
        # Display-only smoothing with coverage normalization avoids black halos
        # beside the silhouette. Keep the label map and measured areas unchanged.
        radius = (0.8**2 + contour_blur**2) ** 0.5
        blurred_color = np.asarray(Image.fromarray(color).filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
        coverage = (
            np.asarray(
                Image.fromarray(active.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(radius)),
                dtype=np.float32,
            )
            / 255.0
        )
        color = np.clip(blurred_color / np.maximum(coverage, 1e-6)[..., None], 0, 255)
    rgb[active] = (rgb[active] * 0.45 + color[active] * 0.55).astype(np.uint8)

    # Keep the legend outside the photograph, showing only zones actually present.
    font_size = max(16, min(28, source.height // 50))
    font = ImageFont.load_default(size=font_size)
    title_font = ImageFont.load_default(size=font_size + 4)
    padding, swatch, row_height = 20, font_size + 4, font_size + 18
    rows = []
    for label, count in zip(*np.unique(labels, return_counts=True), strict=True):
        if label == 0:
            continue
        label = int(label)
        title = names[label].removeprefix("nudenet_").replace("_", " ").title()
        if label >= 7 and label != UNKNOWN:
            title = f"{title} (zone {label})"
        shade = tuple(int(channel) for channel in color_table[label])
        rows.append((f"{title} - {100 * int(count) / max(1, int(inside.sum())):.1f}%", shade))
    text_width = max(font.getbbox(text)[2] for text in [*(text for text, _ in rows), *notes])
    legend_width = max(330, padding * 3 + swatch + text_width)
    legend_height = padding * 3 + row_height * (len(rows) + len(notes) + 1)
    preview = Image.new("RGB", (source.width + legend_width, max(source.height, legend_height)), (24, 27, 34))
    preview.paste(Image.fromarray(rgb), (0, 0))
    draw = ImageDraw.Draw(preview)
    left, top = source.width + padding, padding
    draw.text((left, top), heading, fill="white", font=title_font)
    top += row_height + padding
    for text, shade in rows:
        draw.rectangle((left, top, left + swatch, top + swatch), fill=shade, outline=(220, 220, 220))
        draw.text((left + swatch + padding, top), text, fill=(235, 235, 235), font=font)
        top += row_height
    top += padding
    for text in notes:
        draw.text((left, top), text, fill=(175, 180, 190), font=font)
        top += row_height
    preview.save(destination, quality=95, subsampling=0)


def save_outputs(source, box, inside, raw, zones, reports, output, prefix):
    """Write two legend-bearing previews and an RGBA cutout, without mask exports."""
    x0, y0, x1, y1 = box
    names = {**dict(enumerate(LABELS)), UNKNOWN: "unknown"}
    names.update({r["zone_id"]: f"nudenet_{r['label'].lower()}" for r in reports})
    # Widen before assigning UNKNOWN; uint8 selection can truncate 65535 to 255.
    pascal = raw.astype(np.uint16)
    pascal[~inside] = 0
    pascal[inside & (raw == 0)] = UNKNOWN
    save_preview(
        source,
        box,
        inside,
        pascal,
        names,
        output / f"{prefix}_pascal_overlay.jpg",
        "Pascal-7 anatomy",
        ("Raw coarse anatomy", "No left/right distinction", "Gray: model background inside person"),
    )
    save_preview(
        source,
        box,
        inside,
        zones,
        names,
        output / f"{prefix}_zones_overlay.jpg",
        "Body zones",
        ("Pascal: coarse anatomy", "NudeNet zones: heuristic", "Gray: unknown / unsupported"),
    )

    person = np.zeros((source.height, source.width), dtype=np.uint8)
    person[y0:y1, x0:x1] = inside.astype(np.uint8) * 255
    cutout = source.convert("RGBA")
    cutout.putalpha(Image.fromarray(person))
    cutout.save(output / f"{prefix}_person_cutout.png")
    return {str(key): value for key, value in names.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input-dir", type=Path, required=True)
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("tmp/pascal-nudenet-poc"))
    parser.add_argument("-m", "--model", type=Path, default=Path("tmp/models/schp-pascal-7-int8-static.onnx"))
    parser.add_argument(
        "-p", "--person-model", type=Path, default=Path("tmp/models/yolo26n-seg-ultralytics-v8.4.0.onnx")
    )
    parser.add_argument("-n", "--nudenet-model", type=Path, default=Path("tmp/models/nudenet-320n.onnx"))
    parser.add_argument(
        "-t", "--threads", type=int, default=4, help="Pascal session threads; shared detectors use at most 4"
    )
    parser.add_argument("-r", "--resolution", type=int, default=384, help="Maximum edge-growth dimension")
    parser.add_argument(
        "-c", "--min-confidence", type=float, default=0.35, help="Uncalibrated Pascal top-class score floor"
    )
    parser.add_argument("-s", "--parts-confidence", type=float, default=0.25)
    parser.add_argument("-d", "--no-download", action="store_true")
    parser.add_argument(
        "-w",
        "--overwrite",
        action="store_true",
        help="Delete recognized previous POC outputs in output-dir before writing; refuse unrelated files/subfolders",
    )
    args = parser.parse_args(argv)
    if args.threads < 1 or not 64 <= args.resolution <= 1024:
        parser.error("threads must be positive and resolution must be between 64 and 1024")
    if not 0 <= args.min_confidence <= 1 or not 0 < args.parts_confidence <= 1:
        parser.error("min-confidence must be in [0,1] and parts-confidence in (0,1]")
    if not args.input_dir.is_dir():
        parser.error("input-dir must be an existing directory")
    files = sorted(p for p in args.input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        parser.error("input-dir contains no supported still images")
    if args.output_dir.is_symlink() or args.output_dir.is_junction():
        parser.error("output-dir must not be a symbolic link or junction")
    output = args.output_dir.resolve()
    if args.input_dir.resolve().is_relative_to(output):
        parser.error("output-dir must not be input-dir or an ancestor of input-dir")
    output.mkdir(parents=True, exist_ok=True)
    previous = list(output.iterdir())
    if previous and not args.overwrite:
        parser.error("output-dir must be empty; use --overwrite to delete previous POC outputs")
    # Preflight every entry before removing anything; never recurse or follow links.
    generated_name = re.compile(
        r"\d{4,}_.+_(?:pascal_overlay\.jpg|zones_overlay\.jpg|person_cutout\.png|report\.json|"
        r"person\.png|confidence\.png|(?:pascal|zones)_labels\.png|(?:pascal|zones)_\d+_[a-z_]+\.png)"
    )
    protected = {p.resolve() for p in (*files, args.model, args.person_model, args.nudenet_model)}
    for entry in previous:
        if (
            entry.is_symlink()
            or entry.is_junction()
            or not entry.is_file()
            or entry.resolve().parent != output
            or entry.resolve() in protected
            or not generated_name.fullmatch(entry.name)
        ):
            parser.error(f"Refusing cleanup: unrelated, redirected or non-file output entry: {entry.name}")
    for path, spec in (
        (args.model, PASCAL_SPEC),
        (args.person_model, CHECKPOINTS["person"]),
        (args.nudenet_model, CHECKPOINTS["faces"]),
    ):
        verified_checkpoint(path, spec, not args.no_download)
    from dtflow.services import person_selection as selection

    person_ref = selection.describe_model(args.person_model)
    parts_ref = selection.describe_model(args.nudenet_model)
    person_session = selection._verified_session(person_ref, selection._segmentation_session)
    parts_session = selection._verified_session(parts_ref, selection._faces_session)
    pascal = load_session(args.model, args.threads)
    if any(s.get_providers() != ["CPUExecutionProvider"] for s in (person_session, parts_session, pascal)):
        raise RuntimeError("Only CPUExecutionProvider is permitted")
    # Retain existing results if checkpoint verification or session startup fails.
    for entry in previous:
        if entry.is_symlink() or entry.is_junction() or not entry.is_file() or entry.resolve().parent != output:
            raise RuntimeError(f"Output entry changed before cleanup: {entry.name}")
        entry.unlink()
    if previous:
        print(f"Deleted {len(previous)} previous POC outputs from {output}", flush=True)
    failed = 0
    for index, path in enumerate(files, 1):
        prefix = f"{index:04d}_{path.stem}"
        try:
            source = load_rgb(path)
            people = select_people(selection._instances(person_session, source, 0.25), source.size, 0, 1, False)
            report = {
                "source": path.name,
                "algorithm": "pascal7-nudenet-geodesic-v1",
                "provider": "CPUExecutionProvider",
                "pascal_revision": REVISION,
                "checkpoints": {
                    "pascal": {"sha256": PASCAL_SPEC[3]},
                    "person": {CHECKPOINTS["person"][2]: CHECKPOINTS["person"][3]},
                    "nudenet": {CHECKPOINTS["faces"][2]: CHECKPOINTS["faces"][3]},
                },
                "person_selection": "largest_silhouette",
                "settings": {
                    "resolution": args.resolution,
                    "min_confidence": args.min_confidence,
                    "parts_confidence": args.parts_confidence,
                },
                "people_processed": len(people),
            }
            if people:
                _, mask, person_box = people[0]
                box = tuple(int(v) for v in person_crop_box(person_box, source.size))
                x0, y0, x1, y1 = box
                crop = source.crop(box)
                inside = mask[y0:y1, x0:x1] > 0
                raw, confidence = anatomy(pascal, crop)
                _, detections, _ = detect_body_parts(parts_session, crop, args.parts_confidence)
                zones, details = competing_zones(
                    crop, inside, raw, confidence, detections, args.resolution, args.min_confidence
                )
                names = save_outputs(source, box, inside, raw, zones, details, output, prefix)
                report.update(
                    crop_box=list(box),
                    label_names=names,
                    detections=details,
                    detection_box_coordinates="crop-relative EXIF-oriented pixels",
                    unknown_pixels=int(np.count_nonzero(zones == UNKNOWN)),
                    limitations="Coarse anatomy has no left/right or intimate-part labels; NudeNet zones are heuristic.",
                )
                count = sum(d["pixels"] > 0 for d in details)
                print(f"{path.name}: 1 person, {len(detections)} detections, {count} NudeNet zones", flush=True)
            else:
                report["status"] = "no_person"
                print(f"{path.name}: no person", flush=True)
            (output / f"{prefix}_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            failed += 1
            print(f"{path.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
